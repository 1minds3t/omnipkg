# In omnipkg/i18n.py - Enhanced version with comprehensive language support

import gettext
import os
from importlib import resources

# --- Step 1: Comprehensive language mapping ---
LANGUAGE_CODE_MAP = {
    'aa': 'Afar',
    'af': 'Afrikaans', 
    'am': 'Amharic',
    'ar': 'Arabic',
    'as': 'Assamese',
    'az': 'Azerbaijani',
    'be': 'Belarusian',
    'bg': 'Bulgarian',
    'bin': 'Bini',
    'bn': 'Bangla',
    'bo': 'Tibetan',
    'br': 'Breton',
    'bs': 'Bosnian',
    'ca': 'Catalan',
    'chr': 'Cherokee',
    'ckb': 'Central Kurdish',
    'cmn': 'Chinese',
    'co': 'Corsican',
    'cs': 'Czech',
    'cy': 'Welsh',
    'da': 'Danish',
    'de': 'German',
    'dv': 'Divehi',
    'dz': 'Dzongkha',
    'el': 'Greek',
    'en': 'English',
    'es': 'Spanish',
    'et': 'Estonian',
    'eu': 'Basque',
    'fa': 'Persian',
    'ff': 'Fulah',
    'fi': 'Finnish',
    'fil': 'Filipino',
    'fo': 'Faroese',
    'fr': 'French',
    'fy': 'Western Frisian',
    'ga': 'Irish',
    'gd': 'Scottish Gaelic',
    'gl': 'Galician',
    'gn': 'Guarani',
    'gsw': 'Swiss German',
    'gu': 'Gujarati',
    'ha': 'Hausa',
    'haw': 'Hawaiian',
    'he': 'Hebrew',
    'hi': 'Hindi',
    'hr': 'Croatian',
    'hsb': 'Upper Sorbian',
    'hu': 'Hungarian',
    'hy': 'Armenian',
    'ibb': 'Ibibio',
    'id': 'Indonesian',
    'ig': 'Igbo',
    'ii': 'Sichuan Yi',
    'is': 'Icelandic',
    'it': 'Italian',
    'iu': 'Inuktitut',
    'ja': 'Japanese',
    'ka': 'Georgian',
    'kk': 'Kazakh',
    'kl': 'Kalaallisut',
    'km': 'Khmer',
    'kn': 'Kannada',
    'ko': 'Korean',
    'kok': 'Konkani',
    'kr': 'Kanuri',
    'ks': 'Kashmiri',
    'ky': 'Kyrgyz',
    'la': 'Latin',
    'lb': 'Luxembourgish',
    'ln': 'Lingala',
    'lo': 'Lao',
    'lt': 'Lithuanian',
    'lv': 'Latvian',
    'mi': 'Maori',
    'mk': 'Macedonian',
    'ml': 'Malayalam',
    'mn': 'Mongolian',
    'mni': 'Manipuri',
    'moh': 'Mohawk',
    'mr': 'Marathi',
    'ms': 'Malay',
    'mt': 'Maltese',
    'my': 'Burmese',
    'nb': 'Norwegian Bokmål',
    'ne': 'Nepali',
    'nl': 'Dutch',
    'nn': 'Norwegian Nynorsk',
    'no': 'Norwegian',
    'nso': 'Northern Sotho',
    'nv': 'Navajo',
    'oc': 'Occitan',
    'om': 'Oromo',
    'or': 'Odia',
    'pa': 'Punjabi',
    'pap': 'Papiamento',
    'pl': 'Polish',
    'prs': 'Dari',
    'ps': 'Pashto',
    'pt': 'Portuguese',
    'qu': 'Quechua',
    'quc': 'Kʼicheʼ',
    'quz': 'Quechua',
    'rm': 'Romansh',
    'ro': 'Romanian',
    'ru': 'Russian',
    'rw': 'Kinyarwanda',
    'sa': 'Sanskrit',
    'sah': 'Sakha',
    'sd': 'Sindhi',
    'se': 'Northern Sami',
    'si': 'Sinhala',
    'sk': 'Slovak',
    'sl': 'Slovenian',
    'sm': 'Samoan',
    'sma': 'Southern Sami',
    'smj': 'Lule Sami',
    'smn': 'Inari Sami',
    'sms': 'Skolt Sami',
    'so': 'Somali',
    'sq': 'Albanian',
    'sr': 'Serbian',
    'st': 'Southern Sotho',
    'sv': 'Swedish',
    'sw': 'Swahili',
    'syr': 'Syriac',
    'ta': 'Tamil',
    'te': 'Telugu',
    'tg': 'Tajik',
    'th': 'Thai',
    'ti': 'Tigrinya',
    'tk': 'Turkmen',
    'tl': 'Tagalog',
    'tn': 'Tswana',
    'tr': 'Turkish',
    'ts': 'Tsonga',
    'tt': 'Tatar',
    'ug': 'Uyghur',
    'uk': 'Ukrainian',
    'ur': 'Urdu',
    'uz': 'Uzbek',
    've': 'Venda',
    'vi': 'Vietnamese',
    'wo': 'Wolof',
    'xh': 'Xhosa',
    'yi': 'Yiddish',
    'yo': 'Yoruba',
    'yue': 'Cantonese',
    'zh': 'Chinese',
    'zu': 'Zulu',
    # Specific variants
    'pt_BR': 'Brazilian Portuguese',
    'pt-BR': 'Brazilian Portuguese',
    'zh_CN': 'Chinese (Simplified)',
    'zh-CN': 'Chinese (Simplified, China)',
    'zh_Hans': 'Simplified Chinese',
    'zh-Hans': 'Simplified Chinese',
    'zh_Hant': 'Traditional Chinese',
    'zh-Hant': 'Traditional Chinese',
}

# Separate available languages from coming soon ones
AVAILABLE_LANGUAGES = {
    'en': {'name': 'English', 'native': 'English', 'hello': 'Hello'},
    'am': {'name': 'Amharic', 'native': 'አማርኛ', 'hello': 'ሰላም'},
    'ar': {'name': 'Arabic', 'native': 'العربية', 'hello': 'مرحبا'},
    'ar_eg': {'name': 'Arabic (Egypt)', 'native': 'العربية (مصر)', 'hello': 'مرحبا'},
    'bn': {'name': 'Bengali', 'native': 'বাংলা', 'hello': 'হ্যালো'},
    'da': {'name': 'Danish', 'native': 'Dansk', 'hello': 'Hej'},
    'de': {'name': 'German', 'native': 'Deutsch', 'hello': 'Hallo'},
    'es': {'name': 'Spanish', 'native': 'Español', 'hello': 'Hola'},
    'fr': {'name': 'French', 'native': 'Français', 'hello': 'Bonjour'},
    'hi': {'name': 'Hindi', 'native': 'हिंदी', 'hello': 'नमस्ते'},
    'hr': {'name': 'Croatian', 'native': 'Hrvatski', 'hello': 'Bok'},
    'id': {'name': 'Indonesian', 'native': 'Bahasa Indonesia', 'hello': 'Halo'},
    'it': {'name': 'Italian', 'native': 'Italiano', 'hello': 'Ciao'},
    'ja': {'name': 'Japanese', 'native': '日本語', 'hello': 'こんにちは'},
    'ko': {'name': 'Korean', 'native': '한국어', 'hello': '안녕하세요'},
    'nl': {'name': 'Dutch', 'native': 'Nederlands', 'hello': 'Hallo'},
    'no': {'name': 'Norwegian', 'native': 'Norsk', 'hello': 'Hei'},
    'pl': {'name': 'Polish', 'native': 'Polski', 'hello': 'Cześć'},
    'pt_BR': {'name': 'Portuguese (Brazil)', 'native': 'Português (Brasil)', 'hello': 'Olá'},
    'ru': {'name': 'Russian', 'native': 'Русский', 'hello': 'Привет'},
    'sv': {'name': 'Swedish', 'native': 'Svenska', 'hello': 'Hej'},
    'sw': {'name': 'Swahili', 'native': 'Kiswahili', 'hello': 'Hujambo'},
    'tr': {'name': 'Turkish', 'native': 'Türkçe', 'hello': 'Merhaba'},
    'vi': {'name': 'Vietnamese', 'native': 'Tiếng Việt', 'hello': 'Xin chào'},
    'zh_CN': {'name': 'Chinese (Simplified)', 'native': '中文 (简体)', 'hello': '你好'},
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

# Don't add other languages - only show what's actually available

# This is the variable that your cli.py is trying to import
SUPPORTED_LANGUAGES = {code: data['native'] for code, data in LANG_INFO.items()}

# --- Step 2: Define the Translator class that solves the PyTorch conflict ---
class Translator:
    """
    A callable class that holds the global translation function.
    This structure avoids namespace collisions with libraries like PyTorch.
    """
    def __init__(self):
        self._translator = lambda s: s
        self.current_lang = 'en'
        self.set_language()

    def set_language(self, lang_code=None):
        try:
            with resources.path('omnipkg', 'locale') as locale_dir_path:
                localedir = str(locale_dir_path)

            if lang_code is None:
                import locale
                lang_env = (locale.getlocale()[0] or 'en_US')
                lang_code = lang_env.split('.')[0]

            # Normalize language codes (handle both underscore and hyphen variants)
            if lang_code in LANGUAGE_CODE_MAP:
                normalized_code = lang_code
            elif lang_code.replace('-', '_') in LANGUAGE_CODE_MAP:
                normalized_code = lang_code.replace('-', '_')
            elif lang_code.replace('_', '-') in LANGUAGE_CODE_MAP:
                normalized_code = lang_code.replace('_', '-')
            else:
                normalized_code = lang_code

            langs_to_try = [normalized_code]
            if '_' in normalized_code:
                langs_to_try.append(normalized_code.split('_')[0])
            elif '-' in normalized_code:
                langs_to_try.append(normalized_code.split('-')[0])
            langs_to_try.append('en')

            translation = gettext.translation(
                'omnipkg',
                localedir=localedir,
                languages=langs_to_try,
                fallback=True
            )
            self._translator = translation.gettext
            self.current_lang = translation.info().get('language', 'en')
        except Exception:
            self.current_lang = 'en'
            self._translator = lambda s: s

    def __call__(self, text):
        return self._translator(text)

    def get_language_code(self):
        return self.current_lang

    def get_language_name(self, code=None):
        """Get the human-readable name of a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get('name', LANGUAGE_CODE_MAP.get(code, code))
    
    def get_native_name(self, code=None):
        """Get the native name of a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get('native', LANGUAGE_CODE_MAP.get(code, code))
    
    def get_hello(self, code=None):
        """Get the hello greeting for a language."""
        if code is None:
            code = self.current_lang
        return LANG_INFO.get(code, {}).get('hello', 'Hello')
    
    def is_supported(self, code):
        """Check if a language code is supported."""
        return code in LANGUAGE_CODE_MAP or code in LANG_INFO

# --- Step 3: Create the global instance that your app will import ---
_ = Translator()

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
        return {'name': LANGUAGE_CODE_MAP[code], 'native': LANGUAGE_CODE_MAP[code], 'hello': 'Hello'}
    return None