"""Non-blocking GUI Update Notification Banner, Workers, Changelog Viewer & Installer Dialog."""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import updates
from ..core.paths import APP_VERSION
from .widgets import divider, label


# --------------------------------------------------------------- QThread workers
class UpdateCheckWorker(QThread):
    """Fetches GitHub Release metadata on a background thread without blocking Qt."""

    checkFinished = Signal(dict)

    def __init__(self, url: str = updates.RELEASES_URL, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        result = updates.check_latest(url=self._url)
        self.checkFinished.emit(result)


class UpdateDownloadWorker(QThread):
    """Streams and cryptographically verifies an update `.exe` off the GUI thread."""

    progressChanged = Signal(int, int, str)       # received_bytes, total_bytes, phase
    verified = Signal(object)                     # VerifiedUpdatePackage
    signatureRequired = Signal(str, str, str)     # message, sha256, asset_name
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        release: updates.ReleaseInfo,
        *,
        require_signature: bool = True,
        allow_loopback_test: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._release = release
        self._require_signature = require_signature
        self._allow_loopback_test = allow_loopback_test
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            pkg = updates.download_and_verify_update(
                self._release,
                require_signature=self._require_signature,
                progress=lambda got, total, phase: self.progressChanged.emit(int(got), int(total), str(phase)),
                cancel_event=self._cancel,
                allow_loopback_test=self._allow_loopback_test,
            )
            self.verified.emit(pkg)
        except updates.UpdateCancelledError:
            self.cancelled.emit()
        except updates.UpdateSignatureRequiredError as exc:
            self.signatureRequired.emit(str(exc), exc.sha256, exc.asset_name)
        except Exception as exc:
            self.failed.emit(str(exc))


# ------------------------------------------------------------ notification bar
class UpdateBanner(QFrame):
    """Dismissible top notification banner displayed when a newer version is available."""

    openRequested = Signal(dict)
    dismissed = Signal()

    def __init__(self, lang: str = "fa", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.lang = "en" if lang == "en" else "fa"
        self._data: Dict[str, Any] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(10)

        self._text = QLabel("")
        self._text.setWordWrap(True)
        row.addWidget(self._text, 1)

        self._open_btn = QPushButton("")
        self._open_btn.setObjectName("Primary")
        self._open_btn.clicked.connect(lambda: self.openRequested.emit(dict(self._data)))
        row.addWidget(self._open_btn)

        self._dismiss_btn = QPushButton("×")
        self._dismiss_btn.setObjectName("Ghost")
        self._dismiss_btn.setFixedWidth(34)
        self._dismiss_btn.clicked.connect(self._dismiss)
        row.addWidget(self._dismiss_btn)

        self._retranslate()
        self.hide()

    def set_language(self, lang: str) -> None:
        self.lang = "en" if lang == "en" else "fa"
        self._retranslate()

    def show_release(self, data: Dict[str, Any]) -> None:
        self._data = dict(data)
        self._retranslate()
        self.show()

    def _retranslate(self) -> None:
        latest = str(self._data.get("latest") or "")
        if self.lang == "en":
            self._text.setText(
                f"New release available: WinGenius {latest} (installed: {APP_VERSION})"
                if latest else "A new WinGenius update is available."
            )
            self._open_btn.setText("View changes & update")
            self._dismiss_btn.setToolTip("Dismiss")
        else:
            self._text.setText(
                f"نسخه‌ی جدید WinGenius {latest} منتشر شد (نسخه‌ی فعلی شما: {APP_VERSION})"
                if latest else "نسخه‌ی جدید WinGenius در دسترس است."
            )
            self._open_btn.setText("مشاهده تغییرات و به‌روزرسانی")
            self._dismiss_btn.setToolTip("بستن اعلان")

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()


# --------------------------------------------------------------- update dialog
class UpdateDialog(QDialog):
    """Full update manager: check, view changelog, download, verify signature/hash, install, or rollback."""

    updateLaunched = Signal(dict)

    def __init__(
        self,
        lang: str = "fa",
        initial_check: Optional[Dict[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.lang = "en" if lang == "en" else "fa"
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if self.lang == "fa" else Qt.LayoutDirection.LeftToRight
        )
        self.setWindowTitle(
            "به‌روزرسانی خودکار WinGenius" if self.lang == "fa" else "WinGenius Software Update"
        )
        self.resize(620, 520)

        self._check_data: Optional[Dict[str, Any]] = initial_check
        self._verified_pkg: Optional[updates.VerifiedUpdatePackage] = None
        self._check_worker: Optional[UpdateCheckWorker] = None
        self._dl_worker: Optional[UpdateDownloadWorker] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        mode = updates.detect_install_mode()
        mode_label = {
            "installer": ("نصب‌کننده‌ی ویندوز", "Windows Installer"),
            "portable": ("نسخه‌ی پرتابل", "Portable EXE"),
            "standalone_exe": ("فایل اجرایی مستقل", "Standalone EXE"),
            "source": ("اجرای سورس (Python)", "Source tree"),
        }.get(mode, (mode, mode))

        header_txt = (
            f"نسخه‌ی فعلی: {APP_VERSION}  ·  حالت نصب: {mode_label[0]}"
            if self.lang == "fa"
            else f"Current version: {APP_VERSION}  ·  Install mode: {mode_label[1]}"
        )
        self._header_lbl = label(header_txt, "SectionTitle", wrap=True)
        root.addWidget(self._header_lbl)

        self._status_lbl = label("", "Hint", wrap=True)
        root.addWidget(self._status_lbl)

        self._trust_lbl = label("", "FieldHelp", wrap=True)
        root.addWidget(self._trust_lbl)

        root.addWidget(divider())

        notes_caption = label(
            "تغییرات نسخه (Release Notes):" if self.lang == "fa" else "What's new (Release Notes):",
            "FieldLabel",
        )
        root.addWidget(notes_caption)

        self._changelog = QPlainTextEdit()
        self._changelog.setReadOnly(True)
        self._changelog.setPlaceholderText(
            "پس از بررسی نسخه، فهرست تغییرات در این‌جا نمایش داده می‌شود."
            if self.lang == "fa"
            else "Release notes will appear here after checking GitHub Releases."
        )
        root.addWidget(self._changelog, 1)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        root.addWidget(self._progress)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._check_btn = QPushButton(
            "بررسی مجدد" if self.lang == "fa" else "Check for updates"
        )
        self._check_btn.setObjectName("Ghost")
        self._check_btn.clicked.connect(self.start_check)
        btn_row.addWidget(self._check_btn)

        self._download_btn = QPushButton(
            "دانلود و بررسی امضا" if self.lang == "fa" else "Download & verify"
        )
        self._download_btn.setObjectName("Primary")
        self._download_btn.setEnabled(False)
        self._download_btn.clicked.connect(lambda: self.start_download(require_signature=True))
        btn_row.addWidget(self._download_btn)

        self._cancel_dl_btn = QPushButton(
            "لغو دانلود" if self.lang == "fa" else "Cancel download"
        )
        self._cancel_dl_btn.setObjectName("Ghost")
        self._cancel_dl_btn.hide()
        self._cancel_dl_btn.clicked.connect(self.cancel_download)
        btn_row.addWidget(self._cancel_dl_btn)

        self._install_btn = QPushButton(
            "تأیید و نصب نسخه جدید" if self.lang == "fa" else "Confirm & install"
        )
        self._install_btn.setObjectName("Primary")
        self._install_btn.setEnabled(False)
        self._install_btn.clicked.connect(self.confirm_and_install)
        btn_row.addWidget(self._install_btn)

        self._rollback_btn = QPushButton("")
        self._rollback_btn.setObjectName("Ghost")
        self._rollback_btn.clicked.connect(self.confirm_and_rollback)
        btn_row.addWidget(self._rollback_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton("بستن" if self.lang == "fa" else "Close")
        close_btn.setObjectName("Ghost")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._refresh_rollback_button()
        if initial_check is not None:
            self._apply_check_result(initial_check)
        else:
            self.start_check()

    def _M(self, fa: str, en: str) -> str:
        return en if self.lang == "en" else fa

    def _refresh_rollback_button(self) -> None:
        rb = updates.get_rollback_state()
        if rb and (rb.get("backup_exe_exists") or rb.get("data_backup_exists")):
            prev = str(rb.get("previous_version") or "?")
            self._rollback_btn.setText(
                self._M(f"بازگشت به {prev}", f"Rollback to {prev}")
            )
            self._rollback_btn.show()
        else:
            self._rollback_btn.hide()

    # --------------------------------------------------------------- check step
    def start_check(self) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._verified_pkg = None
        self._install_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._check_btn.setEnabled(False)
        self._status_lbl.setText(
            self._M("در حال دریافت اطلاعات آخرین نسخه از GitHub Releases…",
                    "Checking GitHub Releases for the latest version…")
        )
        self._trust_lbl.setText("")
        worker = UpdateCheckWorker(parent=self)
        worker.checkFinished.connect(self._on_check_finished)
        self._check_worker = worker
        worker.start()

    def _on_check_finished(self, data: Dict[str, Any]) -> None:
        self._check_btn.setEnabled(True)
        self._apply_check_result(data)

    def _apply_check_result(self, data: Dict[str, Any]) -> None:
        self._check_data = dict(data)
        if not data.get("ok"):
            err = str(data.get("error") or "network error")
            self._status_lbl.setText(
                self._M(f"بررسی به‌روزرسانی انجام نشد: {err}", f"Could not check for updates: {err}")
            )
            self._download_btn.setEnabled(False)
            return

        latest = str(data.get("latest") or APP_VERSION)
        body = str(data.get("body") or "").strip()
        self._changelog.setPlainText(
            body or self._M("(توضیحی برای این انتشار ثبت نشده است.)", "(No release notes provided.)")
        )

        if not data.get("update_available"):
            self._status_lbl.setText(
                self._M(
                    f"شما از آخرین نسخه‌ی منتشرشده ({APP_VERSION}) استفاده می‌کنید ✓",
                    f"WinGenius is up to date ({APP_VERSION}) ✓",
                )
            )
            self._download_btn.setEnabled(False)
            return

        asset = data.get("asset") or {}
        size_mb = (int(asset.get("size") or 0) / (1024 * 1024)) if asset else 0.0
        asset_name = str(asset.get("name") or "")
        size_txt = f" ({size_mb:.1f} MB)" if size_mb > 0 else ""
        self._status_lbl.setText(
            self._M(
                f"نسخه‌ی جدید {latest} آماده‌ی دریافت است: {asset_name}{size_txt}",
                f"Update {latest} is available: {asset_name}{size_txt}",
            )
        )
        self._download_btn.setEnabled(bool(asset_name))

    # ------------------------------------------------------------ download step
    def start_download(self, *, require_signature: bool = True) -> None:
        if not self._check_data or not self._check_data.get("update_available"):
            return
        if self._dl_worker is not None and self._dl_worker.isRunning():
            return
        release = updates.release_info_from_check(self._check_data)
        self._verified_pkg = None
        self._install_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._check_btn.setEnabled(False)
        self._cancel_dl_btn.show()
        self._progress.setValue(0)
        self._progress.show()
        self._trust_lbl.setText("")

        worker = UpdateDownloadWorker(release, require_signature=require_signature, parent=self)
        worker.progressChanged.connect(self._on_download_progress)
        worker.verified.connect(self._on_download_verified)
        worker.signatureRequired.connect(self._on_signature_required)
        worker.failed.connect(self._on_download_failed)
        worker.cancelled.connect(self._on_download_cancelled)
        self._dl_worker = worker
        worker.start()

    def cancel_download(self) -> None:
        if self._dl_worker is not None and self._dl_worker.isRunning():
            self._dl_worker.cancel()

    def _on_download_progress(self, got: int, total: int, phase: str) -> None:
        if phase == "verifying":
            self._progress.setRange(0, 0)
            self._status_lbl.setText(
                self._M("در حال بررسی SHA-256، ساختار فایل اجرایی و امضای دیجیتال…",
                        "Verifying SHA-256 digest, PE structure, and digital signature…")
            )
            return
        if total > 0:
            self._progress.setRange(0, 100)
            pct = min(100, int((got * 100) / total))
            self._progress.setValue(pct)
            self._status_lbl.setText(
                self._M(
                    f"در حال دانلود: {got / (1024 * 1024):.1f} از {total / (1024 * 1024):.1f} مگابایت ({pct}٪)",
                    f"Downloading: {got / (1024 * 1024):.1f} / {total / (1024 * 1024):.1f} MB ({pct}%)",
                )
            )
        else:
            self._progress.setRange(0, 0)

    def _reset_dl_controls(self) -> None:
        self._cancel_dl_btn.hide()
        self._check_btn.setEnabled(True)
        self._download_btn.setEnabled(True)
        self._progress.setRange(0, 100)

    def _on_download_verified(self, pkg: updates.VerifiedUpdatePackage) -> None:
        self._reset_dl_controls()
        self._progress.setValue(100)
        self._verified_pkg = pkg
        self._install_btn.setEnabled(True)
        summary = (
            pkg.verification.trust_summary_fa if self.lang == "fa" else pkg.verification.trust_summary_en
        )
        self._status_lbl.setText(
            self._M(
                f"فایل «{pkg.asset_name}» دانلود و تأیید شد. برای نصب دکمه‌ی تأیید را بزنید.",
                f"'{pkg.asset_name}' is downloaded and verified. Click Confirm & install to proceed.",
            )
        )
        self._trust_lbl.setText(f"{summary}\nSHA-256: {pkg.verification.sha256}")
        self._trust_lbl.setStyleSheet("color:#2BD9A0;")

    def _on_signature_required(self, _msg: str, sha256: str, asset_name: str) -> None:
        self._reset_dl_controls()
        self._progress.hide()
        self._trust_lbl.setStyleSheet("color:#FFC24B;")
        self._trust_lbl.setText(
            self._M(
                f"هش SHA-256 فایل ({sha256}) با متادیتای انتشار تطابق داشت، اما امضای دیجیتال مستقل یافت نشد.",
                f"SHA-256 ({sha256}) matched release metadata, but no cryptographic signature was present.",
            )
        )
        prompt = self._M(
            f"هش SHA-256 فایل «{asset_name}» با موفقیت بررسی شد:\n{sha256}\n\n"
            "اما این انتشار فاقد امضای دیجیتال مستقل (Ed25519 / Authenticode) است و به‌طور پیش‌فرض مسدود شد.\n"
            "فقط اگر اثرانگشت SHA-256 بالا را از منبع مستقل تأیید کرده‌اید ادامه دهید. آیا مایل به ادامه هستید؟",
            f"SHA-256 for '{asset_name}' matched release metadata:\n{sha256}\n\n"
            "However, this release carries neither an Ed25519 manifest signature nor Windows Authenticode, "
            "so automatic staging was blocked by default.\n"
            "Proceed ONLY if you independently verified this SHA-256 fingerprint. Continue?",
        )
        ans = QMessageBox.warning(
            self,
            self._M("هشدار امضای دیجیتال", "Signature Verification Warning"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self.start_download(require_signature=False)

    def _on_download_failed(self, error: str) -> None:
        self._reset_dl_controls()
        self._progress.hide()
        self._status_lbl.setText(
            self._M(f"دانلود یا اعتبارسنجی ناموفق بود: {error}", f"Download or verification failed: {error}")
        )
        self._trust_lbl.setStyleSheet("color:#FF6B81;")
        self._trust_lbl.setText(error)

    def _on_download_cancelled(self) -> None:
        self._reset_dl_controls()
        self._progress.hide()
        self._status_lbl.setText(
            self._M("دانلود توسط کاربر لغو شد.", "Download cancelled.")
        )

    # ------------------------------------------------- install & rollback steps
    def confirm_and_install(self) -> None:
        pkg = self._verified_pkg
        if pkg is None:
            return
        prompt = self._M(
            f"نسخه‌ی جدید WinGenius {pkg.version} آماده نصب است.\n\n"
            "• تنظیمات، گاوصندوق‌ها (Vault)، ژورنال و فایل‌های شخصی شما کاملاً حفظ می‌شوند.\n"
            "• پیش از نصب، نسخه‌ی پشتیبان خودکار برای بازیابی احتمالی ذخیره می‌شود.\n"
            "• برنامه بسته شده و نصب‌کننده اجرا خواهد شد.\n\nآیا نصب آغاز شود؟",
            f"WinGenius {pkg.version} is verified and ready to install.\n\n"
            "• Your settings, encrypted Vaults, undo journal, and personal files are preserved.\n"
            "• A full rollback snapshot will be saved automatically before upgrading.\n"
            "• WinGenius will close to apply the update.\n\nStart installation now?",
        )
        ans = QMessageBox.question(
            self,
            self._M("تأیید نصب به‌روزرسانی", "Confirm Update Installation"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            applied = updates.apply_verified_update(pkg)
        except Exception as exc:
            QMessageBox.critical(
                self,
                self._M("خطا در شروع نصب", "Update Launch Failed"),
                str(exc),
            )
            return

        self.updateLaunched.emit(applied)
        self.accept()
        if applied.get("launched"):
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def confirm_and_rollback(self) -> None:
        rb = updates.get_rollback_state()
        if not rb:
            return
        prev = str(rb.get("previous_version") or "?")
        prompt = self._M(
            f"آیا می‌خواهید فایل اجرایی برنامه به نسخه‌ی پشتیبان قبلی ({prev}) بازگردانده شود؟\n"
            "تنظیمات و فایل‌های شخصی شما دست‌نخورده باقی می‌مانند.",
            f"Restore the previous backed-up WinGenius executable (v{prev})?\n"
            "Your settings, Vaults, and personal files will remain intact.",
        )
        ans = QMessageBox.question(
            self,
            self._M("تأیید بازگشت به نسخه قبلی", "Confirm Rollback"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            res = updates.rollback_previous_version()
            QMessageBox.information(
                self,
                self._M("بازگردانی انجام شد", "Rollback Complete"),
                self._M(
                    f"نسخه‌ی {res.get('previous_version')} با موفقیت بازیابی شد.",
                    f"Version {res.get('previous_version')} has been restored.",
                ),
            )
            self._refresh_rollback_button()
        except Exception as exc:
            QMessageBox.critical(self, self._M("خطا در بازگردانی", "Rollback Failed"), str(exc))

    def shutdown_workers(self) -> None:
        for w in (self._dl_worker, self._check_worker):
            if w is not None and w.isRunning():
                if hasattr(w, "cancel"):
                    w.cancel()
                w.quit()
                w.wait(1500)

    def closeEvent(self, event) -> None:
        self.shutdown_workers()
        super().closeEvent(event)
