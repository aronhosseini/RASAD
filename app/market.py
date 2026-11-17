"""دریافت و پردازش داده‌های MarketWatchPlus از tsetmc."""
import io
import os
import re
import ssl
import time

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

URL = "https://old.tsetmc.com/tsev2/excel/MarketWatchPlus.aspx?d=0"
HOME = "https://old.tsetmc.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
              "application/vnd.ms-excel,text/html,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": HOME,
}
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RASAD")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")


def norm(s) -> str:
    return (str(s).replace("ي", "ی").replace("ك", "ک")
            .replace("\u200c", " ").replace("\xa0", " ").strip())


class MarketError(Exception):
    """خطایی که پیام فارسی (msg) و جزئیات فنی (detail) جدا دارد."""

    def __init__(self, msg, detail=""):
        super().__init__(msg)
        self.msg, self.detail = msg, detail


def _legacy_ctx():
    """SSLContext سازگار با سرورهای قدیمی (TLS ضعیف/رنگوسیون قدیمی)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
    except ssl.SSLError:
        pass
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    try:
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    except Exception:
        pass
    return ctx


class _LegacyAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        kw["ssl_context"] = _legacy_ctx()
        super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, proxy, **kw):
        kw["ssl_context"] = _legacy_ctx()
        return super().proxy_manager_for(proxy, **kw)


FLAG_OPTION, FLAG_BLOCK, FLAG_FIXED = 1, 2, 4
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def classify(symbol: str, name: str) -> int:
    """برچسب نوع نماد (بیت‌فلگ):
    1 = اختیار خرید/فروش (ض/ط + عدد، یا «اختیار» در نام)
    2 = معامله‌ی بلوکی/تال (آخر نماد ۲ یا ۳)
    4 = صندوق با درآمد ثابت"""
    sym = norm(symbol).translate(_DIGITS).replace(" ", "")
    nm = norm(name)
    flag = 0
    if re.match(r"^[ضط].*\d{3,}$", sym) or "اختیار" in nm:
        flag |= FLAG_OPTION
    if sym and sym[-1] in "23":
        flag |= FLAG_BLOCK
    if "ثابت" in nm and ("صندوق" in nm or nm.startswith("ص")):
        flag |= FLAG_FIXED
    return flag


def sniff(content: bytes) -> str:
    """تشخیص نوع فایل از روی محتوا: xlsx / xls / html / unknown"""
    if content[:2] == b"PK":
        return "xlsx"
    if content[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"
    head = content[:2000].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<table", b"<?xml", b"<head", b"<body")):
        return "html"
    return "unknown"


def _text(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1256", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _snippet(content: bytes, n=200) -> str:
    t = re.sub(r"<[^>]+>", " ", _text(content))
    return re.sub(r"\s+", " ", t).strip()[:n]


def _proxy_dict(proxy):
    proxy = (proxy or "").strip()
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    if proxy.startswith("socks5://"):
        proxy = proxy.replace("socks5://", "socks5h://", 1)   # DNS هم از طریق پروکسی
    return {"http": proxy, "https": proxy}


def download(proxy=None):
    """فایل اکسل را دانلود می‌کند. خروجی: (محتوا، نوع فایل)
    روش‌ها به ترتیب: HTTPS معمولی، HTTPS با TLS سازگار با سرور قدیمی، HTTP ساده."""
    attempts = [("https", False), ("https", True), ("http", False)]
    errs = []
    for scheme, legacy in attempts:
        tag = scheme + (" legacy-TLS" if legacy else "")
        try:
            sess = requests.Session()
            sess.headers.update(HEADERS)
            pr = _proxy_dict(proxy)
            if pr:
                sess.proxies.update(pr)
            if legacy:
                sess.mount("https://", _LegacyAdapter())
            url = URL if scheme == "https" else URL.replace("https://", "http://", 1)
            r = sess.get(url, timeout=(12, 90), verify=not legacy)
            r.raise_for_status()
            if not r.content:
                raise RuntimeError("empty response")
            return r.content, sniff(r.content)
        except Exception as e:
            errs.append(f"[{tag}] {type(e).__name__}: {str(e)[:220]}")
            time.sleep(1)
    raise MarketError("اتصال به سایت بورس برقرار نشد و فایل دانلود نشد.", "\n".join(errs))


def save_download(content: bytes, kind: str) -> str:
    """آخرین فایل دانلودشده را روی دیسک نگه می‌دارد."""
    ext = {"xlsx": "xlsx", "xls": "xls", "html": "html"}.get(kind, "bin")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    for old in os.listdir(DOWNLOAD_DIR):
        if old.startswith("MarketWatchPlus_latest."):
            try:
                os.remove(os.path.join(DOWNLOAD_DIR, old))
            except OSError:
                pass
    path = os.path.join(DOWNLOAD_DIR, f"MarketWatchPlus_latest.{ext}")
    with open(path, "wb") as f:
        f.write(content)
    return path


def _read_raw(content: bytes, kind: str) -> pd.DataFrame:
    errors = []
    order = {"xlsx": ["openpyxl", "xlrd", "html"],
             "xls": ["xlrd", "openpyxl", "html"],
             "html": ["html"]}.get(kind, ["openpyxl", "xlrd", "html"])
    for how in order:
        try:
            if how == "html":
                txt = _text(content)
                if "<table" not in txt.lower():
                    raise RuntimeError("جدولی در پاسخ سرور نیست")
                tables = pd.read_html(io.StringIO(txt), header=None)
                return max(tables, key=len)
            return pd.read_excel(io.BytesIO(content), header=None, engine=how)
        except Exception as e:
            errors.append(f"{how}: {e}")
    raise MarketError("فایل دریافتی اکسل نیست یا خوانده نشد. پاسخ سرور در پایین آمده است.",
                      f"Server said: {_snippet(content)}\n" + " ; ".join(errors)[:300])


def _pick(names, key, prefer="مقدار"):
    cands = [i for i, n in enumerate(names) if key in n]
    if not cands:
        return None
    for i in cands:
        if prefer in names[i]:
            return i
    return cands[0]


def parse(content: bytes, kind: str = None) -> pd.DataFrame:
    raw = _read_raw(content, kind or sniff(content)).dropna(how="all").reset_index(drop=True)
    raw = raw.map(lambda v: norm(v) if isinstance(v, str) else v) \
        if hasattr(raw, "map") else raw.applymap(lambda v: norm(v) if isinstance(v, str) else v)

    h = None
    for i in range(min(len(raw), 15)):
        if any(isinstance(v, str) and v == "نماد" for v in raw.iloc[i].tolist()):
            h = i
            break
    if h is None:
        raise MarketError("ستون نماد در فایل پیدا نشد. ممکن است ساختار فایل عوض شده باشد.",
                          "header row not found")

    top = raw.iloc[h].copy().ffill()
    top = top.fillna("")
    has_sub = pd.to_numeric(raw.iloc[h + 1], errors="coerce").notna().sum() == 0 \
        if h + 1 < len(raw) else False
    if has_sub:
        sub = raw.iloc[h + 1].fillna("")
        names = [norm(f"{a} {b}") for a, b in zip(top.tolist(), sub.tolist())]
        data = raw.iloc[h + 2:]
    else:
        names = [norm(a) for a in top.tolist()]
        data = raw.iloc[h + 1:]

    i_sym = _pick(names, "نماد")
    i_yes = _pick(names, "دیروز")
    i_last = _pick(names, "آخرین")
    i_fin = _pick(names, "پایانی")
    i_cnt = _pick(names, "تعداد")
    i_name = _pick(names, "نام")
    # جایگزین موقعیتی در صورت تغییر نام ستون‌ها
    i_sym = 0 if i_sym is None else i_sym
    if i_name is None or i_name == i_sym:
        i_name = 1
    i_cnt = 2 if i_cnt is None else i_cnt
    i_yes = 5 if i_yes is None else i_yes
    i_last = 7 if i_last is None else i_last
    i_fin = 10 if i_fin is None else i_fin

    def num(ix):
        return pd.to_numeric(data.iloc[:, ix].astype(str).str.replace(",", ""),
                             errors="coerce")

    df = pd.DataFrame({
        "symbol": data.iloc[:, i_sym].astype(str).str.strip(),
        "name": data.iloc[:, i_name].astype(str),
        "yest": num(i_yes), "last": num(i_last),
        "final": num(i_fin), "count": num(i_cnt),
    })
    df = df[(df.yest > 0) & (df["last"] > 0) & (df["final"] > 0)]
    df = df[df["count"].fillna(0) > 0]          # فقط نمادهای دارای معامله
    if df.empty:
        raise MarketError("هیچ نماد معتبری پیدا نشد. بازار بسته است یا ستون‌ها تغییر کرده‌اند.",
                          "no valid rows after filtering")
    df["flag"] = [classify(a, b) for a, b in zip(df["symbol"], df["name"])]
    df["last_pct"] = (df["last"] / df["yest"] - 1) * 100
    df["final_pct"] = (df["final"] / df["yest"] - 1) * 100
    return df.reset_index(drop=True)


def fetch_market(progress=None, proxy=None):
    """دانلود فایل اکسل -> ذخیره روی دیسک -> خواندن. خروجی: (DataFrame، اطلاعات فایل)"""
    say = progress or (lambda m: None)
    say("در حال دانلود فایل اکسل از سایت بورس...")
    content, kind = download(proxy)
    path = save_download(content, kind)
    say(f"فایل دانلود شد ({len(content) // 1024} KB). در حال خواندن...")
    df = parse(content, kind)
    return df, {"path": path, "size": len(content), "kind": kind}
