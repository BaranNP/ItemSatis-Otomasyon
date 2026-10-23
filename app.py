"""Seller-side order verification desktop app. Python 3.10+"""
from __future__ import annotations

import hashlib
import base64
import ctypes
import html
import json
import secrets
import smtplib
import sqlite3
import ssl
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from tkinter import messagebox, ttk
from licensing import license_summary, validate_license


APP_NAME = "SaticiDogrulama"
APP_DIR = Path.home() / "AppData" / "Local" / APP_NAME
DB_PATH = APP_DIR / "orders.sqlite3"
SECRETS_PATH = APP_DIR / "secrets.json"
CODE_TTL_MINUTES = 20


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    source_buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    encrypted = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "SaticiDogrulama", None, None, None, 0, ctypes.byref(encrypted)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(encrypted.pbData, encrypted.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(encrypted.pbData)


def unprotect_secret(value: str) -> str:
    encrypted_bytes = base64.b64decode(value)
    source_buffer = ctypes.create_string_buffer(encrypted_bytes)
    source = DataBlob(len(encrypted_bytes), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    decrypted = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(decrypted)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(decrypted.pbData)


def db_connect() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_email TEXT NOT NULL,
        buyer_name TEXT NOT NULL DEFAULT '',
        market_order_id TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL,
        details TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        verified_by TEXT
    )""")
    columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
    if "buyer_name" not in columns:
        db.execute("ALTER TABLE orders ADD COLUMN buyer_name TEXT NOT NULL DEFAULT ''")
    if "market_order_id" not in columns:
        db.execute("ALTER TABLE orders ADD COLUMN market_order_id TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE TABLE IF NOT EXISTS product_templates (
        name TEXT PRIMARY KEY,
        details TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return db


def secret(name: str) -> str:
    try:
        values = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        encrypted = values.get(name, "")
        return unprotect_secret(encrypted) if encrypted else ""
    except FileNotFoundError:
        return ""
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Şifreli ayarlar okunamadı: {exc}") from exc


def put_secret(name: str, value: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        values = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        values = {}
    if value:
        values[name] = protect_secret(value)
    else:
        values.pop(name, None)
    SECRETS_PATH.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")


@dataclass
class MailSettings:
    host: str
    port: int
    username: str
    sender: str
    password: str
    tls: bool = True


def send_code(settings: MailSettings, recipient: str, code: str, product: str) -> None:
    if not all((settings.host, settings.username, settings.sender, settings.password)):
        raise ValueError("Önce SMTP ayarlarını doldurup kaydedin.")
    msg = EmailMessage()
    msg["Subject"] = "Sipariş doğrulama kodunuz"
    msg["From"] = settings.sender
    msg["To"] = recipient
    plain_text = (
        f"Merhaba,\n\n{product} siparişiniz için doğrulama kodunuz: {code}\n\n"
        f"Kod {CODE_TTL_MINUTES} dakika geçerlidir ve tek kullanımlıktır. "
        "Kodu İtemsatış sohbetinde satıcıya iletin. Bu işlemi siz başlatmadıysanız e-postayı yok sayın.\n"
    )
    product_html = html.escape(product)
    code_html = html.escape(code)
    html_body = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#172b4d">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden">
    <tr><td style="padding:24px 28px;background:#142b4a;color:#ffffff">
      <div style="font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:#a9c4df">Sipariş güvenliği</div>
      <div style="font-size:23px;font-weight:bold;margin-top:7px">E-posta doğrulaması</div>
    </td></tr>
    <tr><td style="padding:28px">
      <p style="font-size:15px;line-height:1.6;margin:0 0 8px">Merhaba,</p>
      <p style="font-size:15px;line-height:1.6;margin:0 0 18px"><strong>{product_html}</strong> siparişiniz için doğrulama kodunuz:</p>
      <div style="padding:18px;text-align:center;border:1px solid #dbe5ef;border-radius:10px;background:#f8fafc">
        <span style="font-family:Consolas,monospace;font-size:30px;font-weight:bold;letter-spacing:6px;color:#087f8c">{code_html}</span>
      </div>
      <p style="font-size:13px;line-height:1.6;color:#52657a;margin:18px 0 0">Kod <strong>{CODE_TTL_MINUTES} dakika</strong> geçerlidir ve tek kullanımlıktır. Kodu, siparişinizi verdiğiniz platformdaki satıcı sohbetinden iletin.</p>
      <p style="font-size:13px;line-height:1.6;color:#52657a;margin:12px 0 0">Bu doğrulamayı siz başlatmadıysanız e-postayı yok sayın.</p>
    </td></tr>
    <tr><td style="padding:14px 28px;background:#f8fafc;color:#718096;font-size:11px">Bu ileti otomatik gönderilmiştir. Yanıtlamanız gerekmez.</td></tr>
  </table>
</body></html>"""
    msg.set_content(plain_text)
    msg.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.host, settings.port, timeout=20) as smtp:
        if settings.tls:
            smtp.starttls(context=context)
        smtp.login(settings.username, settings.password)
        smtp.send_message(msg)


class SellerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Satıcı İşlem Paneli | İtemsatış yardımcı aracı")
        self.geometry("1040x820")
        self.minsize(900, 700)
        self.last_buyer_name = ""
        self._style()
        self._ui()
        self._load_settings()
        self.refresh_templates()
        self.refresh_orders()

    def _style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(background="#f3f6fb")
        style.configure("TFrame", background="#f3f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f3f6fb", foreground="#172b4d", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#172b4d", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#142b4a", foreground="#ffffff", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#142b4a", foreground="#c6d7ec", font=("Segoe UI", 10))
        style.configure("Section.TLabel", background="#ffffff", foreground="#142b4a", font=("Segoe UI", 12, "bold"))
        style.configure("TNotebook", background="#f3f6fb", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#0d6b72")])
        style.configure("Accent.TButton", background="#087f8c", foreground="#ffffff", padding=(12, 8), font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#066873")])
        style.configure("Treeview", rowheight=30, font=("Segoe UI", 10), background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#e8eef6")

    def _ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        hero = tk.Frame(root, bg="#142b4a", padx=22, pady=18)
        hero.pack(fill="x", pady=(0, 14))
        ttk.Label(hero, text="Satıcı İşlem Paneli", style="Title.TLabel").pack(anchor="w")
        ttk.Label(hero, text="Satışı kaydet → kodu e-postala → sohbet kodunu doğrula → teslimatı kopyala", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        metrics = ttk.Frame(root)
        metrics.pack(fill="x", pady=(0, 12))
        self.pending_metric = self._metric(metrics, "KOD BEKLEYEN", "0", 0)
        self.today_metric = self._metric(metrics, "BUGÜN OLUŞAN", "0", 1)
        self.total_metric = self._metric(metrics, "TOPLAM SİPARİŞ", "0", 2)
        for col in range(3):
            metrics.columnconfigure(col, weight=1)

        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True)
        flow_tab = ttk.Frame(tabs)
        history = ttk.Frame(tabs, padding=14)
        settings = ttk.Frame(tabs, padding=18)
        guide = ttk.Frame(tabs, padding=18)
        tabs.add(flow_tab, text="İşlem Akışı")
        tabs.add(history, text="Sipariş Geçmişi")
        tabs.add(settings, text="E-posta Ayarları")
        tabs.add(guide, text="Kullanım Kılavuzu")
        self._build_guide(guide)

        flow_canvas = tk.Canvas(flow_tab, background="#f3f6fb", highlightthickness=0)
        flow_scroll = ttk.Scrollbar(flow_tab, orient="vertical", command=flow_canvas.yview)
        flow_canvas.configure(yscrollcommand=flow_scroll.set)
        flow_canvas.grid(row=0, column=0, sticky="nsew")
        flow_scroll.grid(row=0, column=1, sticky="ns")
        flow_tab.columnconfigure(0, weight=1)
        flow_tab.rowconfigure(0, weight=1)
        flow = ttk.Frame(flow_canvas, padding=14)
        flow_window = flow_canvas.create_window((0, 0), window=flow, anchor="nw")
        flow.bind("<Configure>", lambda _event: flow_canvas.configure(scrollregion=flow_canvas.bbox("all")))
        flow_canvas.bind("<Configure>", lambda event: flow_canvas.itemconfigure(flow_window, width=event.width))
        flow_canvas.bind("<Enter>", lambda _event: flow_canvas.bind_all("<MouseWheel>", lambda event: flow_canvas.yview_scroll(int(-event.delta / 120), "units")))
        flow_canvas.bind("<Leave>", lambda _event: flow_canvas.unbind_all("<MouseWheel>"))

        native = ttk.Frame(flow, style="Card.TFrame", padding=(14, 10))
        native.pack(fill="x", pady=(0, 10))
        ttk.Label(native, text="Stok kodu/ürünü satıyorsanız İtemsatış'ın stoklu ilan otomatik teslimatını kullanın.", style="Card.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(native, text="Kurulum bilgisini aç", command=lambda: webbrowser.open("https://www.itemsatis.com/sss/stoklu-ilan-nasil-eklenir-65")).pack(side="right", padx=(12, 0))

        manual_note = tk.Frame(flow, bg="#fff7e6", padx=12, pady=9)
        manual_note.pack(fill="x", pady=(0, 10))
        tk.Label(manual_note, text="Not: Bu uygulama şu an İtemsatış'tan satışı veya alıcı e-postasını otomatik çekmiyor. Satışı kontrol edip e-postayı siz girersiniz; düğmeye basınca kod e-postası otomatik gönderilir.", bg="#fff7e6", fg="#704c00", font=("Segoe UI", 9), anchor="w", justify="left", wraplength=940).pack(fill="x")

        order = ttk.Frame(flow, style="Card.TFrame", padding=16)
        order.pack(fill="x", pady=(0, 12))
        ttk.Label(order, text="Yeni satış kaydı", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        buyer_form = ttk.Frame(order, style="Card.TFrame")
        buyer_form.grid(row=1, column=0, sticky="new", padx=(0, 18))
        product_form = ttk.Frame(order, style="Card.TFrame")
        product_form.grid(row=1, column=1, sticky="nsew")
        self.email = self._field(buyer_form, "Alıcı e-postası", 0)
        self.buyer_name = self._field(buyer_form, "İtemsatış kullanıcı adı", 1)
        self.order_ref = self._field(buyer_form, "Sipariş / ilan no (isteğe bağlı)", 2)
        ttk.Label(product_form, text="Ürün şablonu veya ürün adı", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self.product = ttk.Combobox(product_form)
        self.product.grid(row=0, column=1, sticky="ew", pady=4)
        self.product.bind("<<ComboboxSelected>>", self.load_template)
        ttk.Label(product_form, text="TESLİMAT BİLGİSİ · ALICIYA VERİLECEK ÜRÜN BİLGİSİ", style="Card.TLabel", font=("Segoe UI", 10, "bold"), foreground="#087f8c").grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 3))
        ttk.Label(product_form, text="Örn. ürün anahtarı, kullanıcı adı/şifre veya alıcıya özel teslimat notu. Bu içerik kod doğrulanana kadar gönderilmez.", style="Card.TLabel", foreground="#64748b", wraplength=600).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.details = tk.Text(product_form, height=6, wrap="word", font=("Segoe UI", 10), relief="solid", bd=1, bg="#f8fafc", padx=9, pady=8, highlightthickness=1, highlightbackground="#b7c8db", highlightcolor="#087f8c")
        self.details.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        product_form.columnconfigure(1, weight=1)
        actions = ttk.Frame(order, style="Card.TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Şablonu kaydet", command=self.save_template).pack(side="left")
        self.code_request_button = ttk.Button(actions, text="Kod isteme mesajını kopyala", command=self.copy_code_request, state="disabled")
        self.code_request_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Siparişi kaydet ve kodu gönder", style="Accent.TButton", command=self.create_order).pack(side="right")
        order.columnconfigure(0, weight=1)
        order.columnconfigure(1, weight=2)
        order.columnconfigure(3, weight=2)

        verify = ttk.Frame(flow, style="Card.TFrame", padding=16)
        verify.pack(fill="both", expand=True)
        ttk.Label(verify, text="İtemsatış sohbetindeki kodu doğrula", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(verify, text="Alıcının gönderdiği e-posta kodu", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 6))
        self.verify_code_entry = ttk.Entry(verify, width=22, font=("Segoe UI", 11))
        self.verify_code_entry.grid(row=1, column=1, sticky="w", padx=10, pady=(10, 6))
        self.verify_code_entry.bind("<Return>", lambda _event: self.verify_code())
        ttk.Button(verify, text="Doğrula", style="Accent.TButton", command=self.verify_code).grid(row=1, column=2, sticky="e", pady=(10, 6))
        self.delivery_preview = tk.Text(verify, height=5, wrap="word", state="disabled", font=("Segoe UI", 10), relief="solid", bd=1)
        self.delivery_preview.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        ttk.Button(verify, text="Teslimatı kopyala", command=self.copy_delivery).grid(row=2, column=2, sticky="ne", padx=(12, 0))
        ttk.Label(verify, text="İtemsatış sohbetine gönderme, resmi sohbet entegrasyonu olmadığı için sizde kalır.", style="Card.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        verify.columnconfigure(1, weight=1)
        verify.rowconfigure(2, weight=1)

        listing = ttk.Frame(history, style="Card.TFrame", padding=12)
        listing.pack(fill="both", expand=True)
        toolbar = ttk.Frame(listing, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="Sipariş geçmişi", style="Section.TLabel").pack(side="left")
        ttk.Button(toolbar, text="Seçileni sil", command=self.delete_selected_orders).pack(side="right", padx=(8, 0))
        ttk.Button(toolbar, text="Geçmişi sıfırla", command=self.reset_order_history).pack(side="right", padx=(8, 0))
        ttk.Button(toolbar, text="Yenile", command=self.refresh_orders).pack(side="right")
        cols = ("id", "order", "buyer", "product", "email", "status", "created")
        self.table = ttk.Treeview(listing, columns=cols, show="headings", selectmode="extended")
        for key, title, width in (("id", "#", 48), ("order", "Sipariş no", 110), ("buyer", "Alıcı", 140), ("product", "Ürün", 180), ("email", "E-posta", 190), ("status", "Durum", 130), ("created", "Tarih", 145)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="w")
        self.table.pack(fill="both", expand=True)

        settings_card = ttk.Frame(settings, style="Card.TFrame", padding=18)
        settings_card.pack(fill="x", anchor="n")
        ttk.Label(settings_card, text="SMTP bağlantısı", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.smtp_host = self._field(settings_card, "SMTP sunucusu", 1, "smtp.gmail.com")
        self.smtp_port = self._field(settings_card, "Port", 2, "587")
        self.smtp_user = self._field(settings_card, "SMTP kullanıcı / e-posta", 3)
        self.sender = self._field(settings_card, "Gönderen e-posta", 4)
        self.smtp_pass = self._field(settings_card, "SMTP uygulama şifresi", 5, secret=True)
        ttk.Button(settings_card, text="Ayarları kaydet", style="Accent.TButton", command=self.save_settings).grid(row=6, column=1, sticky="e", pady=(12, 0))
        ttk.Label(settings_card, text="Parola bu Windows kullanıcısına özel şifrelenerek saklanır.", style="Card.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 0))
        try:
            ok, _message, payload = validate_license(secret("license_token"), secret, put_secret)
            license_text = license_summary(payload) if ok and payload else "Lisans bilgisi doğrulanamadı."
        except Exception:
            license_text = "Lisans bilgisi okunamadı."
        ttk.Label(settings_card, text=license_text, style="Card.TLabel", foreground="#087f8c").grid(row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _metric(self, parent, label, value, column):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 6 if column < 2 else 0))
        ttk.Label(card, text=label, style="Card.TLabel", foreground="#65758b").pack(anchor="w")
        val = ttk.Label(card, text=value, style="Card.TLabel", font=("Segoe UI", 18, "bold"), foreground="#087f8c")
        val.pack(anchor="w", pady=(3, 0))
        return val

    def _build_guide(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=22)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="İlk kez mi kullanıyorsunuz?", style="Section.TLabel").pack(anchor="w")
        ttk.Label(card, text="Bu uygulama, özel teslimat gereken siparişlerde e-posta kodunu ve teslimat metnini yönetir.", style="Card.TLabel", wraplength=850).pack(anchor="w", pady=(6, 18))
        steps = [
            ("1 · E-posta ayarını bir kez yapın", "E-posta Ayarları sekmesine girin. SMTP sunucusu, port, kullanıcı adı ve gönderen adresini doldurun. Gmail kullanıyorsanız normal hesap parolası yerine Google uygulama şifresi girin. Ayarları kaydedin."),
            ("2 · Satışı İtemsatış'ta doğrulayın", "Önce İtemsatış hesabınızdaki Siparişler/Bildirimler alanından satışın gerçekten oluştuğunu kontrol edin. Bu panel satış ve alıcı e-postasını kendiliğinden algılamaz; bu bilgileri siz girersiniz."),
            ("3 · Sipariş kaydı oluşturup kod gönderin", "İşlem Akışı sekmesinde alıcının e-postasını yazın. İtemsatış kullanıcı adını ve sipariş numarasını eşleştirme için ekleyin. Ürün adını ve teslimat bilgisini girin. Siparişi kaydedip kodu gönderin. Tekrar kullanacağınız ürün bilgisi için Şablonu kaydedin."),
            ("4 · Alıcıya İtemsatış sohbetinden bilgi verin", "Kod isteme mesajını kopyalayın ve alıcının İtemsatış sohbetine yapıştırın. Alıcı e-postasına gelen kodu aynı sohbetten size iletsin. Kod 20 dakika geçerlidir."),
            ("5 · Kodu doğrulayın ve teslimatı gönderin", "Alıcının gönderdiği kodu panelde doğrulayın. Teslimat metnini kopyalayıp alıcıya İtemsatış sohbetinden gönderin. Sohbeti uygulama otomatik okuyup mesaj göndermez."),
            ("Stok kodu mu satıyorsunuz?", "Ürün tek kullanımlık kodlardan oluşan stoklu bir ilansa, İtemsatış'ın kendi otomatik teslimatını kullanın. Böylece platform satın alma sonrasında stoktan teslim eder; bu uygulamadaki e-posta kodu akışına gerek kalmaz."),
        ]
        for title, body in steps:
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=title, style="Card.TLabel", font=("Segoe UI", 11, "bold"), foreground="#087f8c").pack(anchor="w")
            ttk.Label(row, text=body, style="Card.TLabel", wraplength=870, justify="left").pack(anchor="w", pady=(3, 0))
        ttk.Button(card, text="İtemsatış stoklu ilan kılavuzunu aç", command=lambda: webbrowser.open("https://www.itemsatis.com/sss/stoklu-ilan-nasil-eklenir-65")).pack(anchor="w", pady=(16, 0))

    def _field(self, parent, label, row, default="", secret=False):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=3, padx=(0, 12))
        ent = ttk.Entry(parent, show="●" if secret else "")
        ent.insert(0, default)
        ent.grid(row=row, column=1, sticky="ew", pady=3)
        parent.columnconfigure(1, weight=1)
        return ent

    def _load_settings(self):
        self.smtp_pass.insert(0, secret("smtp_password"))
        values = []
        try:
            values = (APP_DIR / "settings.txt").read_text(encoding="utf-8").splitlines()
        except OSError:
            pass
        defaults = ("smtp.gmail.com", "587", "", "")
        fields = (self.smtp_host, self.smtp_port, self.smtp_user, self.sender)
        for field, default, index in zip(fields, defaults, range(4)):
            field.delete(0, "end")
            field.insert(0, values[index] if index < len(values) else default)

    def save_settings(self, quiet=False):
        try:
            int(self.smtp_port.get())
            put_secret("smtp_password", self.smtp_pass.get())
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "settings.txt").write_text("\n".join((self.smtp_host.get(), self.smtp_port.get(), self.smtp_user.get(), self.sender.get())), encoding="utf-8")
            if not quiet:
                messagebox.showinfo("Ayarlar", "E-posta ayarları kaydedildi. Parola Windows kullanıcınıza özel şifrelenir.")
        except Exception as e:
            messagebox.showerror("Ayar kaydedilemedi", str(e))

    def _mail_settings(self):
        return MailSettings(self.smtp_host.get().strip(), int(self.smtp_port.get()), self.smtp_user.get().strip(), self.sender.get().strip(), self.smtp_pass.get())

    def create_order(self):
        email, product = self.email.get().strip(), self.product.get().strip()
        buyer_name, order_ref = self.buyer_name.get().strip(), self.order_ref.get().strip()
        details = self.details.get("1.0", "end").strip()
        if not email or "@" not in email or not product or not details:
            messagebox.showerror("Eksik bilgi", "Geçerli e-posta, ürün adı ve teslimat bilgisi gerekli.")
            return
        self.code_request_button.state(["disabled"])
        code = secrets.token_urlsafe(6).replace("-", "A").replace("_", "B")[:8].upper()
        expires = datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)
        db = db_connect()
        try:
            cur = db.execute("INSERT INTO orders(buyer_email,buyer_name,market_order_id,product,details,code_hash,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)", (email, buyer_name, order_ref, product, details, hashlib.sha256(code.encode()).hexdigest(), expires.isoformat(), datetime.now(timezone.utc).isoformat()))
            db.commit()
            order_id = cur.lastrowid
        finally:
            db.close()
        try:
            send_code(self._mail_settings(), email, code, product)
        except Exception as e:
            db = db_connect()
            db.execute("DELETE FROM orders WHERE id=?", (order_id,))
            db.commit()
            db.close()
            messagebox.showerror("E-posta gönderilemedi", str(e))
            return
        self.email.delete(0, "end")
        self.buyer_name.delete(0, "end")
        self.order_ref.delete(0, "end")
        self.details.delete("1.0", "end")
        self.refresh_orders()
        self.last_buyer_name = buyer_name
        self.code_request_button.state(["!disabled"])
        self._copy_code_request(show_dialog=False)
        messagebox.showinfo("Kod gönderildi", f"Doğrulama kodu e-postaya gönderildi ve kod isteme mesajı panoya kopyalandı. İtemsatış sohbetine geçip Ctrl+V ile gönderin. Kod {CODE_TTL_MINUTES} dakika geçerlidir.")

    def refresh_orders(self):
        for item in self.table.get_children():
            self.table.delete(item)
        db = db_connect()
        db.execute("UPDATE orders SET status='expired' WHERE status='pending' AND expires_at<=?", (datetime.now(timezone.utc).isoformat(),))
        db.commit()
        rows = db.execute("SELECT id,market_order_id,buyer_name,product,buyer_email,status,created_at FROM orders ORDER BY id DESC LIMIT 100").fetchall()
        counts = db.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending FROM orders").fetchone()
        today = datetime.now().astimezone().date().isoformat()
        created_today = db.execute("SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
        db.close()
        self.total_metric.configure(text=str(counts["total"] or 0))
        self.pending_metric.configure(text=str(counts["pending"] or 0))
        self.today_metric.configure(text=str(created_today))
        for row in rows:
            created = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
            status = {"pending": "Kod bekleniyor", "verified": "Doğrulandı", "expired": "Süresi doldu"}.get(row["status"], row["status"])
            self.table.insert("", "end", values=(row["id"], row["market_order_id"], row["buyer_name"], row["product"], row["buyer_email"], status, created))

    def delete_selected_orders(self):
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("Sipariş seçilmedi", "Silmek istediğiniz sipariş satırını seçin. Birden fazla satır da seçebilirsiniz.")
            return
        order_ids = [int(self.table.item(item, "values")[0]) for item in selected]
        if not messagebox.askyesno("Siparişleri sil", f"Seçili {len(order_ids)} sipariş kalıcı olarak silinsin mi?"):
            return
        db = db_connect()
        db.executemany("DELETE FROM orders WHERE id=?", [(order_id,) for order_id in order_ids])
        db.commit()
        db.close()
        self._clear_delivery_preview()
        self.refresh_orders()

    def reset_order_history(self):
        db = db_connect()
        count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        db.close()
        if count == 0:
            messagebox.showinfo("Geçmiş zaten boş", "Silinecek sipariş kaydı bulunmuyor.")
            return
        if not messagebox.askyesno("Sipariş geçmişini sıfırla", f"Toplam {count} sipariş kaydı kalıcı olarak silinecek. Ürün şablonları ve e-posta ayarları korunacak. Devam edilsin mi?"):
            return
        db = db_connect()
        db.execute("DELETE FROM orders")
        db.execute("DELETE FROM sqlite_sequence WHERE name='orders'")
        db.commit()
        db.close()
        self._clear_delivery_preview()
        self.refresh_orders()
        messagebox.showinfo("Geçmiş sıfırlandı", "Sipariş geçmişi temizlendi. Ürün şablonlarınız ve e-posta ayarlarınız korundu.")

    def _clear_delivery_preview(self):
        self.delivery_preview.configure(state="normal")
        self.delivery_preview.delete("1.0", "end")
        self.delivery_preview.configure(state="disabled")

    def refresh_templates(self):
        db = db_connect()
        rows = db.execute("SELECT name FROM product_templates ORDER BY name COLLATE NOCASE").fetchall()
        db.close()
        self.product["values"] = [row["name"] for row in rows]

    def load_template(self, _event=None):
        name = self.product.get().strip()
        if not name:
            return
        db = db_connect()
        row = db.execute("SELECT details FROM product_templates WHERE name=?", (name,)).fetchone()
        db.close()
        if row:
            self.details.delete("1.0", "end")
            self.details.insert("1.0", row["details"])

    def save_template(self):
        name = self.product.get().strip()
        details = self.details.get("1.0", "end").strip()
        if not name or not details:
            messagebox.showerror("Şablon bilgisi eksik", "Şablon adı ve teslimat metni gerekli.")
            return
        db = db_connect()
        db.execute("INSERT INTO product_templates(name,details,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET details=excluded.details, updated_at=excluded.updated_at", (name, details, datetime.now(timezone.utc).isoformat()))
        db.commit()
        db.close()
        self.refresh_templates()
        messagebox.showinfo("Şablon kaydedildi", f"{name} sonraki satışlarda ürün listesinden seçilebilir.")

    def copy_code_request(self):
        self._copy_code_request(show_dialog=True)

    def _copy_code_request(self, show_dialog):
        if not self.last_buyer_name and self.code_request_button.instate(["disabled"]):
            messagebox.showinfo("Önce kod gönderin", "Bu mesaj ancak alıcıya doğrulama kodu e-postayla gönderildikten sonra hazırlanır.")
            return
        buyer = self.last_buyer_name
        greeting = f"Merhaba {buyer}," if buyer else "Merhaba,"
        text = (
            f"{greeting} sipariş güvenliği için e-posta adresinize tek kullanımlık doğrulama kodu gönderdim. "
            f"Lütfen gelen kodu bu İtemsatış sohbetinden paylaşın. Kod {CODE_TTL_MINUTES} dakika geçerlidir."
        )
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        if show_dialog:
            messagebox.showinfo("Mesaj kopyalandı", "İtemsatış sohbetini açıp Ctrl+V ile alıcıya gönderebilirsiniz.")

    def verify_code(self):
        code = self.verify_code_entry.get().strip().upper()
        if not code:
            messagebox.showerror("Kod gerekli", "Alıcının İtemsatış sohbetinde gönderdiği kodu yazın.")
            return
        db = db_connect()
        try:
            row = db.execute("SELECT * FROM orders WHERE code_hash=? AND status='pending'", (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
            if row is None:
                messagebox.showerror("Kod bulunamadı", "Kod geçersiz, süresi dolmuş veya daha önce kullanılmış.")
                return
            if datetime.now(timezone.utc) >= datetime.fromisoformat(row["expires_at"]):
                db.execute("UPDATE orders SET status='expired' WHERE id=?", (row["id"],))
                db.commit()
                self.refresh_orders()
                messagebox.showerror("Kodun süresi doldu", "Yeni bir sipariş doğrulama kodu oluşturun.")
                return
            db.execute("UPDATE orders SET status='verified' WHERE id=?", (row["id"],))
            db.commit()
            self.delivery_preview.configure(state="normal")
            self.delivery_preview.delete("1.0", "end")
            self.delivery_preview.insert("1.0", f"{row['product']}\n{row['details']}")
            self.delivery_preview.configure(state="disabled")
            self.verify_code_entry.delete(0, "end")
            self.refresh_orders()
            messagebox.showinfo("Kod doğrulandı", "Teslimat metni hazır. Kopyalayıp İtemsatış sohbetinde alıcıya gönderin.")
        finally:
            db.close()

    def copy_delivery(self):
        content = self.delivery_preview.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Teslimat yok", "Önce geçerli bir kodu doğrulayın.")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.update()
        messagebox.showinfo("Kopyalandı", "Teslimat metni panoya kopyalandı. İtemsatış sohbetine yapıştırabilirsiniz.")


class LicenseGate(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Satıcı İşlem Paneli | Lisans aktivasyonu")
        self.geometry("560x390")
        self.resizable(False, False)
        self.configure(bg="#f3f6fb")
        outer = ttk.Frame(self, padding=26)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Satıcı İşlem Paneli", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Devam etmek için satıcınızdan aldığınız süreli lisans anahtarını girin.", wraplength=490).pack(anchor="w", pady=(8, 16))
        ttk.Label(outer, text="Lisans anahtarı").pack(anchor="w")
        self.token_box = tk.Text(outer, height=5, wrap="word", font=("Consolas", 9), relief="solid", bd=1)
        self.token_box.pack(fill="x", pady=(5, 10))
        self.status = ttk.Label(outer, text="Anahtar bu bilgisayarda güvenli biçimde saklanır.", wraplength=490)
        self.status.pack(anchor="w", pady=(0, 12))
        ttk.Button(outer, text="Etkinleştir ve aç", command=self.activate).pack(anchor="e")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def activate(self):
        token = self.token_box.get("1.0", "end").strip()
        valid, message, _payload = validate_license(token, secret, put_secret)
        if not valid:
            self.status.configure(text=message, foreground="#b42318")
            return
        try:
            put_secret("license_token", token)
        except Exception as exc:
            self.status.configure(text=f"Lisans kaydedilemedi: {exc}", foreground="#b42318")
            return
        self.withdraw()
        app = SellerApp()
        app.mainloop()
        self.destroy()


def run_app():
    try:
        saved_token = secret("license_token")
        valid, _message, _payload = validate_license(saved_token, secret, put_secret) if saved_token else (False, "Lisans anahtarı gerekli.", None)
    except Exception:
        valid = False
    if valid:
        SellerApp().mainloop()
    else:
        LicenseGate().mainloop()


if __name__ == "__main__":
    run_app()
