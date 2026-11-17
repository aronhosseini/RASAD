"""تطبیق نام شرکت‌های فهرست کاربر با ستون «نام» فایل اکسل بازار."""
import difflib
import re

from market import FLAG_BLOCK, FLAG_FIXED, FLAG_OPTION

_CH = {"ي": "ی", "ك": "ک", "ى": "ی", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "آ": "ا",
       "ؤ": "و", "ئ": "ی"}
for i, d in enumerate("۰۱۲۳۴۵۶۷۸۹"):
    _CH[d] = str(i)
for i, d in enumerate("٠١٢٣٤٥٦٧٨٩"):
    _CH[d] = str(i)
_MAP = str.maketrans(_CH)
_JUNK = re.compile(r"[\u064B-\u065F\u0670\u0640\u200b-\u200f\u202a-\u202e\ufeff\xa0]")
_NONWORD = re.compile(r"[^0-9a-zA-Z\u0621-\u06D3]")
_PAREN = re.compile(r"\(.*?\)")


def _clean(s) -> str:
    return _JUNK.sub("", str(s).translate(_MAP))


def key_full(s) -> str:
    return _NONWORD.sub("", _clean(s)).lower()


def key_strip(s) -> str:
    """کلید بدون متن داخل پرانتز (مثلاً «(سهامی عام)»)."""
    return _NONWORD.sub("", _PAREN.sub("", _clean(s))).lower()


def load_names(path: str):
    """خواندن فهرست نام شرکت‌ها (هر خط یک نام). خطوط خالی و خطوط شروع‌شده با # نادیده گرفته می‌شوند."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1256"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k = key_full(line)
        if k and k not in seen:
            seen.add(k)
            out.append(line)
    return out


def match_companies(df, names):
    """برای هر نام فهرست، ردیف بازار را پیدا می‌کند. خروجی: لیست دیکشنری هم‌ترتیب با names.
    ردیف‌های بلوکی/تال، اختیار و صندوق درآمد ثابت هیچ‌وقت انتخاب نمی‌شوند.
    اگر چند نماد هم‌نام بودند، نمادی که تعداد معامله‌ی بیشتری دارد انتخاب می‌شود."""
    mask = FLAG_OPTION | FLAG_BLOCK | FLAG_FIXED
    pool = df[(df["flag"] & mask) == 0].to_dict("records")
    idx_full, idx_strip = {}, {}
    for r in pool:
        idx_full.setdefault(key_full(r["name"]), []).append(r)
        idx_strip.setdefault(key_strip(r["name"]), []).append(r)
    keys = list(idx_full)
    rows = []
    for name in names:
        kf, ks = key_full(name), key_strip(name)
        status = "دقیق"
        cands = idx_full.get(kf) or idx_strip.get(ks)
        if not cands:
            close = difflib.get_close_matches(kf, keys, n=1, cutoff=0.92)
            if close:
                cands, status = idx_full[close[0]], "تقریبی"
        if cands:
            r = max(cands, key=lambda x: x["count"])
            rows.append({"name": name, "symbol": r["symbol"], "yest": r["yest"],
                         "last": r["last"], "last_pct": float(r["last_pct"]),
                         "count": r["count"], "status": status})
        else:
            rows.append({"name": name, "symbol": "", "yest": None, "last": None,
                         "last_pct": None, "count": None, "status": "یافت نشد"})
    return rows
