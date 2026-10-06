"""App Store locale -> storefront mapping and country names."""

from __future__ import annotations

# ASC localization locale -> storefronts where it is indexed.
# The first storefront is the primary one; the rest are optional secondaries.
LOCALE_STOREFRONTS: dict[str, list[str]] = {
    "ar-SA": ["sa", "ae", "eg", "kw", "qa"],
    "ca": ["es"],
    "cs": ["cz"],
    "da": ["dk"],
    "de-DE": ["de", "at", "ch"],
    "el": ["gr"],
    "en-AU": ["au", "nz"],
    "en-CA": ["ca"],
    "en-GB": ["gb", "ie"],
    "en-US": ["us"],
    "es-ES": ["es"],
    "es-MX": ["mx", "ar", "co", "cl", "pe"],
    "fi": ["fi"],
    "fr-CA": ["ca"],
    "fr-FR": ["fr", "be", "ch"],
    "he": ["il"],
    "hi": ["in"],
    "hr": ["hr"],
    "hu": ["hu"],
    "id": ["id"],
    "it": ["it"],
    "ja": ["jp"],
    "ko": ["kr"],
    "ms": ["my"],
    "nl-NL": ["nl", "be"],
    "no": ["no"],
    "pl": ["pl"],
    "pt-BR": ["br"],
    "pt-PT": ["pt"],
    "ro": ["ro"],
    "ru": ["ru", "kz", "by"],
    "sk": ["sk"],
    "sl-SI": ["si"],
    "sv": ["se"],
    "th": ["th"],
    "tr": ["tr"],
    "uk": ["ua"],
    "vi": ["vn"],
    "zh-Hans": ["cn"],
    "zh-Hant": ["tw", "hk"],
    "bn-BD": ["bd"],
    "ur-PK": ["pk"],
}

COUNTRIES: dict[str, str] = {
    "ae": "UAE", "ar": "Argentina", "at": "Austria", "au": "Australia",
    "bd": "Bangladesh", "be": "Belgium", "br": "Brazil", "by": "Belarus",
    "ca": "Canada", "ch": "Switzerland", "cl": "Chile", "cn": "China",
    "co": "Colombia", "cz": "Czechia", "de": "Germany", "dk": "Denmark",
    "eg": "Egypt", "es": "Spain", "fi": "Finland", "fr": "France",
    "gb": "United Kingdom", "gr": "Greece", "hk": "Hong Kong", "hr": "Croatia",
    "hu": "Hungary", "id": "Indonesia", "ie": "Ireland", "il": "Israel",
    "in": "India", "it": "Italy", "jp": "Japan", "kr": "South Korea",
    "kw": "Kuwait", "kz": "Kazakhstan", "mx": "Mexico", "my": "Malaysia",
    "ng": "Nigeria", "nl": "Netherlands", "no": "Norway", "nz": "New Zealand",
    "pe": "Peru", "ph": "Philippines", "pk": "Pakistan", "pl": "Poland",
    "pt": "Portugal", "qa": "Qatar", "ro": "Romania", "ru": "Russia",
    "sa": "Saudi Arabia", "se": "Sweden", "sg": "Singapore", "si": "Slovenia",
    "sk": "Slovakia", "th": "Thailand", "tr": "Türkiye", "tw": "Taiwan",
    "ua": "Ukraine", "us": "United States", "vn": "Vietnam", "za": "South Africa",
}


def storefronts_for(locale: str, include_secondary: bool) -> list[str]:
    stores = LOCALE_STOREFRONTS.get(locale)
    if not stores:
        # Unknown locale like "xx-YY": fall back to its region part.
        region = locale.split("-")[-1].lower()
        stores = [region] if region in COUNTRIES else []
    return stores if include_secondary else stores[:1]
