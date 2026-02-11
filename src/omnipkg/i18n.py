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


# --- Step 2: Define the Translator class that solves the PyTorch conflict ---
class Translator:
    """
    A callable class that holds the global translation function.
    This structure avoids namespace collisions with libraries like PyTorch.
    """

    def __init__(self):
        self._translator = lambda s: s
        self.current_lang = "en"
        # DON'T auto-call set_language here!

    def set_language(self, lang_code=None):
        debug = os.environ.get("OMNIPKG_DEBUG") == "1"
        
        if debug:
            print(_('[DEBUG-I18N] ======== set_language() called ========'), file=sys.stderr)
            print(_('[DEBUG-I18N] lang_code parameter: {!r}').format(lang_code), file=sys.stderr)
            print(_('[DEBUG-I18N] OMNIPKG_LANG env var: {!r}').format(os.environ.get('OMNIPKG_LANG')), file=sys.stderr)
            print(_('[DEBUG-I18N] current_lang before: {!r}').format(self.current_lang), file=sys.stderr)
        
        try:
            with resources.path("omnipkg", "locale") as locale_dir_path:
                localedir = str(locale_dir_path)
            
            if debug:
                print(_('[DEBUG-I18N] localedir: {}').format(localedir), file=sys.stderr)

            # Priority order: passed lang_code > env var > system locale
            if lang_code is None:
                lang_code = os.environ.get("OMNIPKG_LANG")
                if debug:
                    print(_('[DEBUG-I18N] After checking env, lang_code: {!r}').format(lang_code), file=sys.stderr)
            
            if lang_code is None:
                import locale
                lang_env = locale.getlocale()[0] or "en_US"
                lang_code = lang_env.split(".")[0]
                if debug:
                    print(_('[DEBUG-I18N] Using system locale: {!r}').format(lang_code), file=sys.stderr)

            # Normalize language codes (handle both underscore and hyphen variants)
            if lang_code in LANGUAGE_CODE_MAP:
                normalized_code = lang_code
            elif lang_code.replace("-", "_") in LANGUAGE_CODE_MAP:
                normalized_code = lang_code.replace("-", "_")
            elif lang_code.replace("_", "-") in LANGUAGE_CODE_MAP:
                normalized_code = lang_code.replace("_", "-")
            else:
                normalized_code = lang_code

            langs_to_try = [normalized_code]
            if "_" in normalized_code:
                langs_to_try.append(normalized_code.split("_")[0])
            elif "-" in normalized_code:
                langs_to_try.append(normalized_code.split("-")[0])
            langs_to_try.append("en")

            if debug:
                print(_('[DEBUG-I18N] normalized_code: {!r}').format(normalized_code), file=sys.stderr)
                print(f"[DEBUG-I18N] langs_to_try: {langs_to_try}", file=sys.stderr)

            translation = gettext.translation(
                "omnipkg", localedir=localedir, languages=langs_to_try, fallback=True
            )
            self._translator = translation.gettext

            # Don't trust .info()["language"] - it's often wrong/missing
            # Instead, check which .mo file actually exists
            for lang in langs_to_try:
                from pathlib import Path
                mo_file = Path(localedir) / lang / "LC_MESSAGES" / "omnipkg.mo"
                if mo_file.exists():
                    self.current_lang = lang
                    break
            else:
                self.current_lang = "en"
            
            if debug:
                print(_('[DEBUG-I18N] Translation loaded successfully'), file=sys.stderr)
                print(_('[DEBUG-I18N] current_lang after: {!r}').format(self.current_lang), file=sys.stderr)
                print(_('[DEBUG-I18N] ======== set_language() complete ========'), file=sys.stderr)
                
        except Exception as e:
            if debug:
                print(_('[DEBUG-I18N] ERROR in set_language: {}').format(e), file=sys.stderr)
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
    print(f'[DEBUG-I18N] ======== Creating _ instance ========', file=sys.stderr)
    print(f'[DEBUG-I18N] OMNIPKG_LANG at module load: {os.environ.get("OMNIPKG_LANG")!r}', file=sys.stderr)

_ = Translator()

# CRITICAL: Initialize with environment variable if set
_initial_lang = os.environ.get("OMNIPKG_LANG")
if debug:
    print(f"[DEBUG-I18N] _initial_lang from env: {_initial_lang!r}", file=sys.stderr)

if _initial_lang:
    if debug:
        print(_('[DEBUG-I18N] Calling _.set_language({!r})').format(_initial_lang), file=sys.stderr)
    _.set_language(_initial_lang)
else:
    if debug:
        print(f"[DEBUG-I18N] No OMNIPKG_LANG set, calling _.set_language() with defaults", file=sys.stderr)
    _.set_language()

if debug:
    print(_('[DEBUG-I18N] Final _.current_lang: {!r}').format(_.current_lang), file=sys.stderr)
    print(_('[DEBUG-I18N] ======== i18n module loaded ========'), file=sys.stderr)


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