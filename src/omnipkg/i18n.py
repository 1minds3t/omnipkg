from __future__ import annotations  # Python 3.6+ compatibility

from omnipkg.common_utils import safe_print

try:
    from .common_utils import safe_print
except ImportError:
    pass

# In omnipkg/i18n.py - Enhanced version with comprehensive language support

import gettext
import os
import sys
from importlib import resources

# --- Step 1: Comprehensive language mapping ---
LANGUAGE_CODE_MAP = {
    "aa": "Afar",
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "as": "Assamese",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bin": "Bini",
    "bn": "Bangla",
    "bo": "Tibetan",
    "br": "Breton",
    "bs": "Bosnian",
    "ca": "Catalan",
    "chr": "Cherokee",
    "ckb": "Central Kurdish",
    "cmn": "Chinese",
    "co": "Corsican",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "dv": "Divehi",
    "dz": "Dzongkha",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "ff": "Fulah",
    "fi": "Finnish",
    "fil": "Filipino",
    "fo": "Faroese",
    "fr": "French",
    "fy": "Western Frisian",
    "ga": "Irish",
    "gd": "Scottish Gaelic",
    "gl": "Galician",
    "gn": "Guarani",
    "gsw": "Swiss German",
    "gu": "Gujarati",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hsb": "Upper Sorbian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "ibb": "Ibibio",
    "id": "Indonesian",
    "ig": "Igbo",
    "ii": "Sichuan Yi",
    "is": "Icelandic",
    "it": "Italian",
    "iu": "Inuktitut",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "kl": "Kalaallisut",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "kok": "Konkani",
    "kr": "Kanuri",
    "ks": "Kashmiri",
    "ky": "Kyrgyz",
    "la": "Latin",
    "lb": "Luxembourgish",
    "ln": "Lingala",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mi": "Maori",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mni": "Manipuri",
    "moh": "Mohawk",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "nb": "Norwegian Bokmål",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "nso": "Northern Sotho",
    "nv": "Navajo",
    "oc": "Occitan",
    "om": "Oromo",
    "or": "Odia",
    "pa": "Punjabi",
    "pap": "Papiamento",
    "pl": "Polish",
    "prs": "Dari",
    "ps": "Pashto",
    "pt": "Portuguese",
    "qu": "Quechua",
    "quc": "Kʼicheʼ",
    "quz": "Quechua",
    "rm": "Romansh",
    "ro": "Romanian",
    "ru": "Russian",
    "rw": "Kinyarwanda",
    "sa": "Sanskrit",
    "sah": "Sakha",
    "sd": "Sindhi",
    "se": "Northern Sami",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sm": "Samoan",
    "sma": "Southern Sami",
    "smj": "Lule Sami",
    "smn": "Inari Sami",
    "sms": "Skolt Sami",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "st": "Southern Sotho",
    "sv": "Swedish",
    "sw": "Swahili",
    "syr": "Syriac",
    "ta": "Tamil",
    "te": "Telugu",
    "tg": "Tajik",
    "th": "Thai",
    "ti": "Tigrinya",
    "tk": "Turkmen",
    "tl": "Tagalog",
    "tn": "Tswana",
    "tr": "Turkish",
    "ts": "Tsonga",
    "tt": "Tatar",
    "ug": "Uyghur",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "ve": "Venda",
    "vi": "Vietnamese",
    "wo": "Wolof",
    "xh": "Xhosa",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "yue": "Cantonese",
    "zh": "Chinese",
    "zu": "Zulu",
    # Specific variants
    "pt_BR": "Brazilian Portuguese",
    "pt-BR": "Brazilian Portuguese",
    "zh_CN": "Chinese (Simplified)",
    "zh-CN": "Chinese (Simplified, China)",
    "zh_Hans": "Simplified Chinese",
    "zh-Hans": "Simplified Chinese",
    "zh_Hant": "Traditional Chinese",
    "zh-Hant": "Traditional Chinese",
}

# Separate available languages from coming soon ones
AVAILABLE_LANGUAGES = {
    "en": {"name": "English", "native": "English", "hello": "Hello"},
    "am": {"name": "Amharic", "native": "አማርኛ", "hello": "ሰላም"},
    "ar": {"name": "Arabic", "native": "العربية", "hello": "مرحبا"},
    "ar_eg": {"name": "Arabic (Egypt)", "native": "العربية (مصر)", "hello": "مرحبا"},
    "bn": {"name": "Bengali", "native": "বাংলা", "hello": "হ্যালো"},
    "da": {"name": "Danish", "native": "Dansk", "hello": "Hej"},
    "de": {"name": "German", "native": "Deutsch", "hello": "Hallo"},
    "es": {"name": "Spanish", "native": "Español", "hello": "Hola"},
    "fr": {"name": "French", "native": "Français", "hello": "Bonjour"},
    "hi": {"name": "Hindi", "native": "हिंदी", "hello": "नमस्ते"},
    "hr": {"name": "Croatian", "native": "Hrvatski", "hello": "Bok"},
    "id": {"name": "Indonesian", "native": "Bahasa Indonesia", "hello": "Halo"},
    "it": {"name": "Italian", "native": "Italiano", "hello": "Ciao"},
    "ja": {"name": "Japanese", "native": "日本語", "hello": "こんにちは"},
    "ko": {"name": "Korean", "native": "한국어", "hello": "안녕하세요"},
    "nl": {"name": "Dutch", "native": "Nederlands", "hello": "Hallo"},
    "no": {"name": "Norwegian", "native": "Norsk", "hello": "Hei"},
    "pl": {"name": "Polish", "native": "Polski", "hello": "Cześć"},
    "pt_BR": {
        "name": "Portuguese (Brazil)",
        "native": "Português (Brasil)",
        "hello": "Olá",
    },
    "ru": {"name": "Russian", "native": "Русский", "hello": "Привет"},
    "sv": {"name": "Swedish", "native": "Svenska", "hello": "Hej"},
    "sw": {"name": "Swahili", "native": "Kiswahili", "hello": "Hujambo"},
    "tr": {"name": "Turkish", "native": "Türkçe", "hello": "Merhaba"},
    "vi": {"name": "Vietnamese", "native": "Tiếng Việt", "hello": "Xin chào"},
    "zh_CN": {"name": "Chinese (Simplified)", "native": "中文 (简体)", "hello": "你好"},
}

# Coming soon languages - just show names, no selection
COMING_SOON_LANGUAGES = []
for code, name in LANGUAGE_CODE_MAP.items():
    if code not in AVAILABLE_LANGUAGES:
        COMING_SOON_LANGUAGES.append(name)

# Sort coming soon alphabetically
COMING_SOON_LANGUAGES.sort()

# Use AVAILABLE_LANGUAGES as LANG_INFO for backward compatibility
LANG_INFO = AVAILABLE_LANGUAGES

# This is the variable that your cli.py is trying to import
SUPPORTED_LANGUAGES = {code: data["native"] for code, data in LANG_INFO.items()}

# --- Case-insensitive language code normalization ---
# Build a lowercase lookup: "zh_cn" -> "zh_CN", "pt_br" -> "pt_BR", etc.
_LANG_CODE_LOWER: "dict[str, str]" = {k.lower(): k for k in SUPPORTED_LANGUAGES}


def normalize_language_code(code: str) -> "str | None":
    """
    Normalize a user-supplied language code to its canonical form.

    Handles:
      - Wrong case:           "zh_cn"  -> "zh_CN"
      - Hyphen vs underscore: "zh-CN"  -> "zh_CN"
      - Already correct:      "zh_CN"  -> "zh_CN"
      - Unknown code:         returns None

    Use this everywhere a language code comes in from user input or env vars.
    """
    if not code:
        return None
    # Exact match
    if code in SUPPORTED_LANGUAGES:
        return code
    # Normalise separators then do a case-insensitive lookup
    for variant in (code.replace("-", "_"), code.replace("_", "-")):
        canonical = _LANG_CODE_LOWER.get(variant.lower())
        if canonical:
            return canonical
    return None


# --- Step 2: Define the Translator class that solves the PyTorch conflict ---
class Translator:
    """
    A callable class that holds the global translation function.
    This structure avoids namespace collisions with libraries like PyTorch.
    """

    # Class-level cache shared across all instances and reloads.
    # Keys are canonical lang codes; values are (gettext_fn, resolved_lang).
    _cache: "dict[str, tuple]" = {}

    def __init__(self):
        self._translator = lambda s: s
        self.current_lang = "en"
        self._debug_logged = False  # suppress repeat debug output
        # DON'T auto-call set_language here!

    def set_language(self, lang_code=None):
        debug = os.environ.get("OMNIPKG_DEBUG") == "1" and not self._debug_logged

        if debug:
            print(f'[DEBUG-I18N] set_language({lang_code!r}) lang={os.environ.get("OMNIPKG_LANG")!r} current={self.current_lang!r}', file=sys.stderr)

        # ── Resolve the effective lang code first (cheap) ─────────────────
        effective = lang_code
        if effective is None:
            effective = os.environ.get("OMNIPKG_LANG")
        if not effective:
            # Fast path: skip locale.getlocale() (costs 10-20ms) when no lang
            # is configured. locale.getlocale() reads locale files just to tell
            # us "en_US" — we'd fall through to "en" anyway via the langs_to_try
            # fallback list. Only call it if someone actually set a locale.
            _lc = os.environ.get("LANG") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
            if _lc:
                effective = _lc.split(".")[0].split("@")[0]
            else:
                effective = "en"

        effective = normalize_language_code(effective) or effective

        # ── Fast path: already loaded this lang, just point at cached fn ──
        if effective in Translator._cache:
            cached_fn, resolved_lang = Translator._cache[effective]
            if self.current_lang == resolved_lang and self._translator is cached_fn:
                # Nothing to do — same lang already active on this instance
                if debug:
                    print(f'[DEBUG-I18N] set_language done -> {self.current_lang!r} (cache hit, no-op)', file=sys.stderr)
                    self._debug_logged = True
                return
            self._translator = cached_fn
            self.current_lang = resolved_lang
            if debug:
                print(f'[DEBUG-I18N] set_language done -> {self.current_lang!r} (cache hit)', file=sys.stderr)
                self._debug_logged = True
            return

        # ── Slow path: first time seeing this lang — do the gettext work ──
        try:
            try:
                localedir = str(resources.files("omnipkg") / "locale")
            except AttributeError:
                import pathlib
                localedir = str(pathlib.Path(__file__).parent / "locale")

            langs_to_try = [effective]
            if "_" in effective:
                langs_to_try.append(effective.split("_")[0])
            elif "-" in effective:
                langs_to_try.append(effective.split("-")[0])
            langs_to_try.append("en")

            if debug:
                print(f'[DEBUG-I18N] localedir={localedir} normalized={effective!r} langs={langs_to_try}', file=sys.stderr)

            translation = gettext.translation(
                "omnipkg", localedir=localedir, languages=langs_to_try, fallback=True
            )
            fn = translation.gettext

            resolved_lang = "en"
            for lang in langs_to_try:
                from pathlib import Path
                mo_file = Path(localedir) / lang / "LC_MESSAGES" / "omnipkg.mo"
                if mo_file.exists():
                    resolved_lang = lang
                    break

            # Store in class-level cache so every future instance/reload is instant
            Translator._cache[effective] = (fn, resolved_lang)

            self._translator = fn
            self.current_lang = resolved_lang

            if debug:
                print(f'[DEBUG-I18N] set_language done -> {self.current_lang!r}', file=sys.stderr)
                self._debug_logged = True

        except Exception as e:
            if debug:
                print(f'[DEBUG-I18N] ERROR in set_language: {e}', file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
            self.current_lang = "en"
            self._translator = lambda s: s

    def __call__(self, text):
        return self._translator(text)

    def get_language_code(self):
        return self.current_lang

    def get_language_name(self, code=None):
        """Get the human-readable name of a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get("name", LANGUAGE_CODE_MAP.get(code, code))

    def get_native_name(self, code=None):
        """Get the native name of a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get("native", LANGUAGE_CODE_MAP.get(code, code))

    def get_hello(self, code=None):
        """Get the hello greeting for a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get("hello", "Hello")

    def is_supported(self, code):
        """Check if a language code is supported."""
        return code in LANGUAGE_CODE_MAP or code in LANG_INFO


# --- Step 3: Create the global instance that your app will import ---
debug = os.environ.get("OMNIPKG_DEBUG") == "1"
if debug:
    print(f'[DEBUG-I18N] i18n init OMNIPKG_LANG={os.environ.get("OMNIPKG_LANG")!r}', file=sys.stderr)

_ = Translator()

# CRITICAL: Initialize with environment variable if set
_initial_lang = os.environ.get("OMNIPKG_LANG")
if _initial_lang:
    # Normalize at the source so everything downstream gets the canonical code
    _initial_lang = normalize_language_code(_initial_lang) or _initial_lang
    os.environ["OMNIPKG_LANG"] = _initial_lang
_.set_language(_initial_lang)

if debug:
    print(f'[DEBUG-I18N] i18n loaded -> lang={_.current_lang!r}', file=sys.stderr)


# Convenience function to get all supported language codes
def get_supported_language_codes():
    """Return a list of all supported language codes."""
    return list(LANG_INFO.keys())


# Convenience function to get language info
def get_language_info(code):
    """Get complete language information for a given code."""
    if code in LANG_INFO:
        return LANG_INFO[code]
    elif code in LANGUAGE_CODE_MAP:
        return {
            "name": LANGUAGE_CODE_MAP[code],
            "native": LANGUAGE_CODE_MAP[code],
            "hello": "Hello",
        }
    return None