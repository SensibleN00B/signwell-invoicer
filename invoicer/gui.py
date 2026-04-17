"""CustomTkinter GUI for bulk invoice sending.

Designed to be distributed as a standalone .exe.
Configuration files (.env, clients.yaml, sent.sqlite) live next to the exe.

Threading model:
  - UI runs on the main thread (tkinter requirement).
  - send_invoice() runs in a daemon thread (it blocks for ~2s per invoice).
  - Worker pushes dicts into self._queue.
  - self.after(100, _process_queue) polls the queue and updates widgets safely.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import BooleanVar, StringVar, filedialog, messagebox

import customtkinter as ctk

from invoicer.config import Client, ClientsRegistry, Settings, infer_client_key
from invoicer.sender import send_invoice, sha256_file
from invoicer.signwell import SignWellError
from invoicer.tracking import Tracker


def get_app_dir() -> Path:
    """Directory containing the .exe (when frozen) or cwd (dev mode)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


@dataclass
class InvoiceItem:
    pdf_path: Path
    client_key: str | None
    client: Client | None
    status: str = "─"
    selected: bool = True
    document_id: str | None = None          # populated from tracker at scan time
    _pdf_url: str = ""                      # stashed during refresh for download worker
    checkbox_var: BooleanVar = field(default_factory=BooleanVar)
    status_label: ctk.CTkLabel | None = field(default=None, repr=False)
    checkbox_widget: ctk.CTkCheckBox | None = field(default=None, repr=False)
    row_frame: ctk.CTkFrame | None = field(default=None, repr=False)


class InvoicerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        os.chdir(get_app_dir())

        self.title("SignWell Invoicer")
        self.geometry("980x660")
        self.minsize(760, 500)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._items: list[InvoiceItem] = []
        self._settings: Settings | None = None
        self._registry: ClientsRegistry | None = None
        self._queue: queue.Queue = queue.Queue()
        self._is_sending = False
        self._is_refreshing = False
        self._is_downloading = False
        self._signed_folder_var = StringVar()

        self._build_ui()
        self._load_settings()
        self.after(100, self._process_queue)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- Top controls panel ---
        controls = ctk.CTkFrame(self)
        controls.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 5))
        controls.grid_columnconfigure(1, weight=1)

        # Row 0: PDF folder
        ctk.CTkLabel(controls, text="PDF Folder:", width=90, anchor="e").grid(
            row=0, column=0, padx=(12, 6), pady=8
        )
        self._dir_var = StringVar()
        ctk.CTkEntry(controls, textvariable=self._dir_var).grid(
            row=0, column=1, padx=4, pady=8, sticky="ew"
        )
        ctk.CTkButton(controls, text="Browse", width=80, command=self._browse_dir).grid(
            row=0, column=2, padx=4, pady=8
        )
        ctk.CTkButton(
            controls, text="Scan", width=80,
            fg_color="transparent", border_width=2,
            command=self._scan,
        ).grid(row=0, column=3, padx=(4, 12), pady=8)

        # Row 1: Clients file + mode toggle
        ctk.CTkLabel(controls, text="Clients:", width=90, anchor="e").grid(
            row=1, column=0, padx=(12, 6), pady=8
        )
        self._clients_var = StringVar(value=str(get_app_dir() / "clients.yaml"))
        ctk.CTkEntry(controls, textvariable=self._clients_var).grid(
            row=1, column=1, padx=4, pady=8, sticky="ew"
        )
        ctk.CTkButton(controls, text="Browse", width=80, command=self._browse_clients).grid(
            row=1, column=2, padx=4, pady=8
        )
        self._mode_var = StringVar(value="TEST")
        ctk.CTkSegmentedButton(
            controls, values=["TEST", "PROD"], variable=self._mode_var, width=140
        ).grid(row=1, column=3, padx=(4, 12), pady=8)

        # Row 2: Signed Folder
        ctk.CTkLabel(controls, text="Signed Folder:", width=90, anchor="e").grid(
            row=2, column=0, padx=(12, 6), pady=8
        )
        ctk.CTkEntry(controls, textvariable=self._signed_folder_var).grid(
            row=2, column=1, padx=4, pady=8, sticky="ew"
        )
        ctk.CTkButton(
            controls, text="Browse", width=80, command=self._browse_signed_folder
        ).grid(row=2, column=2, padx=4, pady=8)

        # --- Scrollable invoice table ---
        self._table = ctk.CTkScrollableFrame(self, fg_color=("gray90", "gray17"))
        self._table.grid(row=1, column=0, sticky="nsew", padx=15, pady=4)

        # Column header row
        header_row = ctk.CTkFrame(self._table, fg_color="transparent")
        header_row.pack(fill="x", padx=6, pady=(4, 2))
        ctk.CTkLabel(header_row, text="", width=28).pack(side="left")
        for label, width in [("PDF File", 280), ("Name", 170), ("Email", 230), ("Status", 110)]:
            ctk.CTkLabel(
                header_row, text=label, width=width, anchor="w",
                text_color="gray60", font=("", 12, "bold"),
            ).pack(side="left", padx=4)
        self._table_header = header_row

        # --- Actions bar ---
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=15, pady=4)

        self._send_btn = ctk.CTkButton(
            actions, text="Send Selected", width=160, height=36,
            fg_color="#2d7a2d", hover_color="#255c25",
            command=self._send_selected,
        )
        self._send_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions, text="Select All", width=110, height=36,
            fg_color="transparent", border_width=2,
            command=self._select_all,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            actions, text="Clear All", width=110, height=36,
            fg_color="transparent", border_width=2,
            command=self._clear_all,
        ).pack(side="left")

        ctk.CTkFrame(actions, fg_color="transparent", width=16).pack(side="left")

        self._refresh_btn = ctk.CTkButton(
            actions, text="↻", width=36, height=36,
            fg_color="transparent", border_width=2,
            font=ctk.CTkFont(size=18),
            command=self._refresh_statuses,
        )
        self._refresh_btn.pack(side="left", padx=(0, 6))

        self._download_btn = ctk.CTkButton(
            actions, text="Download Signed", width=150, height=36,
            fg_color="#1565c0", hover_color="#0d47a1",
            command=self._download_signed,
        )
        self._download_btn.pack(side="left")

        self._count_label = ctk.CTkLabel(actions, text="", text_color="gray60")
        self._count_label.pack(side="right")

        # --- Log panel ---
        self._log = ctk.CTkTextbox(self, height=110, state="disabled", wrap="word")
        self._log.grid(row=3, column=0, sticky="ew", padx=15, pady=(4, 15))

    # ── Startup ────────────────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        try:
            self._settings = Settings()
            self._mode_var.set(
                "TEST" if self._settings.default_mode == "test" else "PROD"
            )
            self._log_write("Ready. Browse to a PDF folder and click Scan.")
        except Exception as exc:
            self._log_write(f"⚠ Settings warning: {exc}")
            self._log_write("  Make sure .env is in the same folder as this app.")

    # ── Browse buttons ─────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="Select PDF folder")
        if path:
            self._dir_var.set(path)
            self._scan()

    def _browse_clients(self) -> None:
        path = filedialog.askopenfilename(
            title="Select clients YAML",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
        )
        if path:
            self._clients_var.set(path)

    def _browse_signed_folder(self) -> None:
        path = filedialog.askdirectory(title="Select folder for signed PDFs")
        if path:
            self._signed_folder_var.set(path)

    # ── Scan ───────────────────────────────────────────────────────────────────

    def _scan(self) -> None:
        dir_str = self._dir_var.get().strip()
        if not dir_str:
            self._log_write("⚠ Enter a PDF folder path first.")
            return
        dir_path = Path(dir_str)
        if not dir_path.is_dir():
            self._log_write(f"⚠ Not a directory: {dir_path}")
            return

        clients_str = self._clients_var.get().strip()
        if clients_str:
            try:
                self._registry = ClientsRegistry.load(Path(clients_str))
                self._log_write(
                    f"Loaded {len(self._registry.clients)} client(s)"
                    f" from {Path(clients_str).name}"
                )
            except Exception as exc:
                self._log_write(f"⚠ Clients file error: {exc}")
                self._registry = None

        pdf_files = sorted(dir_path.glob("*.pdf"))
        if not pdf_files:
            self._log_write(f"No PDF files found in {dir_path}")
            return

        self._log_write(f"Found {len(pdf_files)} PDF(s) in {dir_path.name}/")

        # Destroy previous row frames (tracked per-item — avoids touching
        # CTkScrollableFrame's internal canvas/scrollbar widgets).
        for item in self._items:
            if item.row_frame is not None:
                item.row_frame.destroy()
        self._items.clear()

        tracker = Tracker(self._settings.db_path) if self._settings else None
        test_mode = self._mode_var.get() == "TEST"

        for pdf_path in pdf_files:
            client_key = None
            client_obj = None
            if self._registry:
                client_key = infer_client_key(pdf_path.name, self._registry)
                if client_key:
                    try:
                        client_obj = self._registry.get(client_key)
                    except KeyError:
                        pass

            var = BooleanVar(value=client_key is not None)
            item = InvoiceItem(
                pdf_path=pdf_path,
                client_key=client_key,
                client=client_obj,
                selected=client_key is not None,
                checkbox_var=var,
            )

            if tracker and client_key:
                file_hash = sha256_file(pdf_path)
                prior = tracker.find_by_file_hash(file_hash, test_mode=test_mode, client_key=client_key)
                if prior is not None:
                    item.document_id = prior["document_id"]
                    prior_status = prior["status"]
                    if prior_status in ("sent", "completed", "downloaded"):
                        item.selected = False
                        item.checkbox_var.set(False)
                    if prior_status == "sent":
                        item.status = "✓ sent"
                    elif prior_status == "completed":
                        item.status = "✓ signed"
                    elif prior_status == "downloaded":
                        item.status = "✓ downloaded"

            self._items.append(item)
            self._add_row(item)

        matched = sum(1 for it in self._items if it.client_key)
        self._log_write(f"{matched}/{len(self._items)} PDFs matched to clients.")
        self._update_count_label()

    def _add_row(self, item: InvoiceItem) -> None:
        row = ctk.CTkFrame(self._table, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)
        item.row_frame = row

        status_colors = {
            "✓ sent": "#4caf50",
            "✓ signed": "#66bb6a",
            "✓ downloaded": "#26a69a",
            "⚠ already sent": "#ff9800",
            "✗ error": "#f44336",
            "✗ declined": "#e53935",
            "✗ cancelled": "#e53935",
            "sending…": "#90caf9",
        }
        checkbox_enabled = item.client_key is not None and item.status == "─"
        text_color = ("gray10", "gray90") if item.client_key else "gray50"

        def on_toggle() -> None:
            item.selected = item.checkbox_var.get()
            self._update_count_label()

        checkbox = ctk.CTkCheckBox(
            row, text="", variable=item.checkbox_var,
            width=28, command=on_toggle,
            state="normal" if checkbox_enabled else "disabled",
        )
        checkbox.pack(side="left")
        item.checkbox_widget = checkbox

        ctk.CTkLabel(
            row, text=item.pdf_path.name, width=280, anchor="w", text_color=text_color,
        ).pack(side="left", padx=4)

        ctk.CTkLabel(
            row,
            text=item.client.name if item.client else "—",
            width=170, anchor="w", text_color=text_color,
        ).pack(side="left", padx=4)

        ctk.CTkLabel(
            row,
            text=str(item.client.email) if item.client else "—",
            width=230, anchor="w", text_color=text_color,
        ).pack(side="left", padx=4)

        initial_color = status_colors.get(item.status, "gray50")
        status_lbl = ctk.CTkLabel(row, text=item.status, width=110, anchor="w", text_color=initial_color)
        status_lbl.pack(side="left", padx=4)
        item.status_label = status_lbl

    # ── Selection helpers ──────────────────────────────────────────────────────

    def _select_all(self) -> None:
        for item in self._items:
            if item.client_key and item.status == "─":
                item.checkbox_var.set(True)
                item.selected = True
        self._update_count_label()

    def _clear_all(self) -> None:
        for item in self._items:
            if item.status == "─":
                item.checkbox_var.set(False)
                item.selected = False
        self._update_count_label()

    def _update_count_label(self) -> None:
        count = sum(1 for it in self._items if it.selected and it.client_key)
        self._count_label.configure(text=f"{count} selected" if count else "")

    # ── Send ───────────────────────────────────────────────────────────────────

    def _send_selected(self) -> None:
        if self._is_sending:
            return

        pending = [
            it for it in self._items
            if it.selected and it.client_key and it.status == "─"
        ]
        if not pending:
            self._log_write("Nothing to send. Select matched PDFs first.")
            return

        test_mode = self._mode_var.get() == "TEST"

        if not test_mode:
            confirmed = messagebox.askyesno(
                "Send in PRODUCTION mode?",
                f"Send {len(pending)} invoice(s) in PRODUCTION mode?\n\n"
                "Real emails will be delivered to clients.",
                icon="warning",
            )
            if not confirmed:
                return

        self._is_sending = True
        self._send_btn.configure(state="disabled", text="Sending…")
        mode_label = "TEST" if test_mode else "PROD"
        self._log_write(f"Sending {len(pending)} invoice(s) in {mode_label} mode…")

        threading.Thread(
            target=self._send_worker,
            args=(pending, test_mode),
            daemon=True,
        ).start()

    def _send_worker(self, pending: list[InvoiceItem], test_mode: bool) -> None:
        """Runs in background thread. Never touches widgets directly."""
        if not self._settings:
            self._queue.put({"type": "log", "text": "⚠ Settings not loaded. Check .env."})
            self._queue.put({"type": "done"})
            return

        tracker = Tracker(self._settings.db_path)

        for item in pending:
            if not item.client or not item.client_key:
                continue

            self._queue.put({"type": "status", "item": item, "status": "sending…"})

            try:
                result = send_invoice(
                    pdf_path=item.pdf_path,
                    client_key=item.client_key,
                    client=item.client,
                    settings=self._settings,
                    tracker=tracker,
                    test_mode=test_mode,
                )
                if result.already_sent:
                    self._queue.put({"type": "status", "item": item, "status": "⚠ already sent"})
                    self._queue.put({
                        "type": "log",
                        "text": f"⚠ {item.pdf_path.name} — already sent",
                    })
                else:
                    self._queue.put({"type": "status", "item": item, "status": "✓ sent"})
                    self._queue.put({
                        "type": "log",
                        "text": f"✓ {item.pdf_path.name} → {result.document_id[:12]}…",
                    })
            except Exception as exc:
                self._queue.put({"type": "status", "item": item, "status": "✗ error"})
                self._queue.put({"type": "log", "text": f"✗ {item.pdf_path.name}: {exc}"})

        self._queue.put({"type": "done"})

    def _process_queue(self) -> None:
        """Called every 100 ms on the main thread. Drains the worker queue."""
        status_colors = {
            "✓ sent": "#4caf50",
            "✓ signed": "#66bb6a",
            "✓ downloaded": "#26a69a",
            "⚠ already sent": "#ff9800",
            "✗ error": "#f44336",
            "✗ declined": "#e53935",
            "✗ cancelled": "#e53935",
            "sending…": "#90caf9",
        }
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg["type"] == "log":
                    self._log_write(msg["text"])
                elif msg["type"] == "status":
                    item: InvoiceItem = msg["item"]
                    item.status = msg["status"]
                    item._pdf_url = msg.get("pdf_url", "")
                    if item.status_label:
                        item.status_label.configure(
                            text=item.status,
                            text_color=status_colors.get(item.status, "gray60"),
                        )
                elif msg["type"] == "done":
                    self._is_sending = False
                    self._send_btn.configure(state="normal", text="Send Selected")
                    self._log_write("Done.")
                elif msg["type"] == "refresh_done":
                    self._is_refreshing = False
                    self._refresh_btn.configure(state="normal")
                    self._log_write("Refresh complete.")
                elif msg["type"] == "download_done":
                    self._is_downloading = False
                    self._download_btn.configure(state="normal", text="Download Signed")
                    self._log_write("Download complete.")
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    # ── Log ────────────────────────────────────────────────────────────────────

    def _log_write(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    # ── Refresh statuses ───────────────────────────────────────────────────────

    def _refresh_statuses(self) -> None:
        if self._is_refreshing or not self._settings:
            return
        self._is_refreshing = True
        self._refresh_btn.configure(state="disabled")
        self._log_write("Refreshing statuses…")
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        from invoicer.signwell import SignWellClient

        items_with_id = [it for it in self._items if it.document_id]
        if not items_with_id:
            self._queue.put({"type": "log", "text": "No tracked documents to refresh."})
            self._queue.put({"type": "refresh_done"})
            return

        tracker = Tracker(self._settings.db_path)
        try:
            with SignWellClient(self._settings.signwell_api_key) as sw:
                for item in items_with_id:
                    try:
                        doc = sw.get_document(item.document_id)
                        api_status = doc.get("status", "")
                        if api_status == "completed":
                            tracker.update_status(item.document_id, "completed")
                            pdf_url = doc.get("completed_pdf_url") or ""
                            self._queue.put({
                                "type": "status", "item": item,
                                "status": "✓ signed", "pdf_url": pdf_url,
                            })
                        elif api_status in ("declined", "cancelled"):
                            tracker.update_status(item.document_id, api_status)
                            self._queue.put({
                                "type": "status", "item": item,
                                "status": f"✗ {api_status}", "pdf_url": "",
                            })
                    except Exception as exc:
                        self._queue.put({
                            "type": "log",
                            "text": f"⚠ Refresh error for {item.document_id[:12]}…: {exc}",
                        })
        finally:
            self._queue.put({"type": "refresh_done"})

    # ── Download signed ────────────────────────────────────────────────────────

    def _download_signed(self) -> None:
        signed_folder_str = self._signed_folder_var.get().strip()
        if not signed_folder_str:
            self._log_write("⚠ Set a Signed Folder before downloading.")
            return
        signed_folder = Path(signed_folder_str)
        if not signed_folder.is_dir():
            self._log_write(f"⚠ Not a directory: {signed_folder}")
            return
        if self._is_downloading or not self._settings:
            return

        targets = [it for it in self._items if it.status == "✓ signed" and it.document_id]
        if not targets:
            self._log_write("No signed invoices ready to download. Run ↻ Refresh first.")
            return

        self._is_downloading = True
        self._download_btn.configure(state="disabled", text="Downloading…")
        self._log_write(f"Downloading {len(targets)} signed PDF(s)…")
        threading.Thread(
            target=self._download_worker, args=(targets, signed_folder), daemon=True,
        ).start()

    def _download_worker(self, targets: list[InvoiceItem], signed_folder: Path) -> None:
        from invoicer.downloader import download_signed_pdf
        from invoicer.signwell import SignWellClient

        tracker = Tracker(self._settings.db_path)
        try:
            with SignWellClient(self._settings.signwell_api_key) as sw:
                for item in targets:
                    try:
                        pdf_url = item._pdf_url
                        if not pdf_url:
                            doc = sw.get_document(item.document_id)
                            pdf_url = doc.get("completed_pdf_url", "")
                        if not pdf_url:
                            self._queue.put({
                                "type": "log",
                                "text": f"⚠ No PDF URL for {item.pdf_path.name}",
                            })
                            continue
                        saved = download_signed_pdf(
                            document_id=item.document_id,
                            pdf_url=pdf_url,
                            signed_folder=signed_folder,
                            tracker=tracker,
                            sw_client=sw,
                        )
                        self._queue.put({
                            "type": "status", "item": item,
                            "status": "✓ downloaded", "pdf_url": "",
                        })
                        self._queue.put({"type": "log", "text": f"✓ Saved: {saved}"})
                    except Exception as exc:
                        self._queue.put({"type": "log", "text": f"✗ {item.pdf_path.name}: {exc}"})
        finally:
            self._queue.put({"type": "download_done"})


def run_gui() -> None:
    app = InvoicerApp()
    app.mainloop()
