"""Stealth/fingerprint reduction utilities for human-mimic browser sessions."""

import json
import re


def _human_mimic_stealth_init_script(
    mimic_locale: str = "en-US",
    *,
    ua_platform: str = "macOS",
    navigator_platform: str = "MacIntel",
    chrome_major: int = 133,
) -> str:
    """Return a small, defensive init-script bundle for human-mimic sessions.

    Goal: reduce trivial automation fingerprints without altering control flow or adding
    retry loops. The script is intentionally conservative and wrapped in try/catch blocks.
    """
    locale = str(mimic_locale or "en-US").strip() or "en-US"
    lang_base = locale.split("-")[0] if "-" in locale else locale
    languages = []
    for candidate in (locale, lang_base, "en-US", "en"):
        c = str(candidate or "").strip()
        if c and c not in languages:
            languages.append(c)
    langs_literal = json.dumps(languages)
    normalized_platform = str(ua_platform or "").strip() or "macOS"
    normalized_navigator_platform = str(navigator_platform or "").strip() or "MacIntel"
    if normalized_platform.lower() not in {"macos", "windows", "linux"}:
        normalized_platform = "macOS"
    # Keep major-version profile coherent with selected UA.
    try:
        major = int(chrome_major)
    except Exception:
        major = 133
    if major < 120 or major > 160:
        major = 133
    major_text = str(major)
    full_version = f"{major}.0.0.0"
    if normalized_platform.lower() == "windows":
        platform_version = "10.0.0"
    elif normalized_platform.lower() == "linux":
        platform_version = "6.0.0"
    else:
        platform_version = "15.0.0"

    ua_brands_literal = json.dumps(
        [
            {"brand": "Not A(Brand", "version": "99"},
            {"brand": "Chromium", "version": major_text},
            {"brand": "Google Chrome", "version": major_text},
        ]
    )
    navigator_platform_literal = json.dumps(normalized_navigator_platform)
    ua_platform_literal = json.dumps(normalized_platform)
    platform_version_literal = json.dumps(platform_version)
    full_version_literal = json.dumps(full_version)
    return (
        "(function(){"
        "const safeDefine=(obj,key,getter)=>{try{Object.defineProperty(obj,key,{get:getter,configurable:true});}catch(e){}};"
        "try{if(window.Navigator&&Navigator.prototype){safeDefine(Navigator.prototype,'webdriver',()=>undefined);}}catch(e){}"
        "try{if(!window.chrome){window.chrome={};} if(!window.chrome.runtime){window.chrome.runtime={};}}catch(e){}"
        "try{if(window.chrome&&!window.chrome.app){window.chrome.app={isInstalled:false};}}catch(e){}"
        f"try{{const langs={langs_literal}; if(window.Navigator&&Navigator.prototype){{safeDefine(Navigator.prototype,'languages',()=>langs.slice());}}}}catch(e){{}}"
        "try{if(window.Navigator&&Navigator.prototype){safeDefine(Navigator.prototype,'plugins',()=>[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}]);}}catch(e){}"
        f"try{{if(window.Navigator&&Navigator.prototype){{safeDefine(Navigator.prototype,'platform',()=>{navigator_platform_literal});}}}}catch(e){{}}"
        "try{if(window.Navigator&&Navigator.prototype){safeDefine(Navigator.prototype,'vendor',()=>'Google Inc.');}}catch(e){}"
        "try{if(window.Navigator&&Navigator.prototype){safeDefine(Navigator.prototype,'maxTouchPoints',()=>0);}}catch(e){}"
        "try{if(window.Navigator&&Navigator.prototype){safeDefine(Navigator.prototype,'hardwareConcurrency',()=>8);}}catch(e){}"
        "try{if(window.Navigator&&Navigator.prototype&&typeof Navigator.prototype.userAgentData==='undefined'){"
        f"const _brands={ua_brands_literal}; const _platform={ua_platform_literal}; const _platformVersion={platform_version_literal}; const _uaFullVersion={full_version_literal};"
        "const makeUAData=()=>({brands:_brands.slice(),mobile:false,platform:_platform,"
        "getHighEntropyValues: async (hints)=>{"
        "const out={brands:_brands.slice(),mobile:false,platform:_platform};"
        "try{for(const h of (Array.isArray(hints)?hints:[])){if(h==='platform') out.platform=_platform; if(h==='platformVersion') out.platformVersion=_platformVersion; if(h==='architecture') out.architecture='x86'; if(h==='bitness') out.bitness='64'; if(h==='model') out.model=''; if(h==='uaFullVersion') out.uaFullVersion=_uaFullVersion; if(h==='fullVersionList') out.fullVersionList=_brands.map((b)=>({brand:b.brand,version:_uaFullVersion}));}}catch(e){}"
        "return out;},toJSON(){return {brands:_brands.slice(),mobile:false,platform:_platform};}});"
        "safeDefine(Navigator.prototype,'userAgentData',makeUAData);"
        "}}catch(e){}"
        "try{if(window.Notification&&typeof window.Notification.permission==='string'&&window.navigator&&window.navigator.permissions&&window.navigator.permissions.query){"
        "const origQuery=window.navigator.permissions.query.bind(window.navigator.permissions);"
        "window.navigator.permissions.query=(params)=>{"
        "try{if(params&&params.name==='notifications'){return Promise.resolve({state:window.Notification.permission});}}catch(e){}"
        "return origQuery(params);"
        "};"
        "}}catch(e){}"
        "})();"
    )


def _human_mimic_chromium_user_agent() -> str:
    """Return a non-headless Chromium user agent for human-mimic sessions."""
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )


def derive_ua_stealth_profile(user_agent: str) -> dict:
    """Infer a coherent stealth profile from the selected UA string."""
    ua = str(user_agent or "")
    lower = ua.lower()
    chrome_major = 133
    match = re.search(r"chrome/(\d+)\.", lower)
    if match is not None:
        try:
            chrome_major = int(match.group(1))
        except Exception:
            chrome_major = 133
    if "windows" in lower:
        return {
            "ua_platform": "Windows",
            "navigator_platform": "Win32",
            "chrome_major": chrome_major,
        }
    if "linux" in lower and "android" not in lower:
        return {
            "ua_platform": "Linux",
            "navigator_platform": "Linux x86_64",
            "chrome_major": chrome_major,
        }
    return {
        "ua_platform": "macOS",
        "navigator_platform": "MacIntel",
        "chrome_major": chrome_major,
    }
