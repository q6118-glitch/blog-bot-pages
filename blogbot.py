#!/usr/bin/env python3
"""Blogger automation (single file).
  python blogbot.py post [en|ar]   publish (respects daily caps)
  python blogbot.py setup          create required pages + build themes into dist/
  python blogbot.py theme          rebuild themes only
  python blogbot.py token          one-time: get G_REFRESH_TOKEN (run on a computer, needs G_CLIENT_ID/G_CLIENT_SECRET)
Env: GLM_API_KEY (primary writer), GEMINI_API_KEY (fallback), G_CLIENT_ID, G_CLIENT_SECRET, G_REFRESH_TOKEN, BLOG_ID_EN, BLOG_ID_AR,
     BLOG_URL_EN, BLOG_URL_AR, CONTACT_EMAIL
Optional knobs (env / GitHub repo variables): GLM_MODEL, GLM_THINKING, DRAFT=1, REVIEW=0, WORDS_MIN, WORDS_MAX,
     RELATED_LINKS, RAMP1, RAMP2, MAX_PER_DAY, SKIP_CHANCE, GEMINI_MODEL, FORCE=1
"""
import os, sys, re, json, time, random, hashlib, base64, html, datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parent
DATA, DIST = ROOT / "data", ROOT / "dist"
MODEL = os.getenv("GEMINI_MODEL", "")   # optional: force one Gemini model first
FORCE = os.getenv("FORCE") == "1"

def E(name, default=""):
    return os.getenv(name) or default

# ================================================================ SETTINGS (all the knobs in one place)
SET = {
    "words_min": int(E("WORDS_MIN", 1800)),      # article length range
    "words_max": int(E("WORDS_MAX", 2500)),
    "review": E("REVIEW", "1") == "1",           # second pass that removes doubtful steps
    "draft": E("DRAFT", "0") == "1",             # 1 = save as drafts in Blogger so you can review before publishing
    "min_conf": int(E("MIN_CONFIDENCE", 8)),     # reviewer confidence (1-10) needed to publish live; lower goes to drafts
    "related_links": int(E("RELATED_LINKS", 3)),
    "grounding": E("GROUNDING", "1") == "1",       # use Gemini + Google Search for live research and fact-checking
    "trends": E("TRENDS", "1") == "1",             # look at Google Trends (last 24h) for topics
    "trend_share": float(E("TREND_SHARE", 0.35)),  # share of articles taken from trends when a fitting one exists # internal links to older posts at the end of each article
    "ramp1": int(E("RAMP1", 10)),                # posts before going from 1/day to 2/day
    "ramp2": int(E("RAMP2", 40)),                # posts before going to MAX_PER_DAY
    "max_per_day": int(E("MAX_PER_DAY", 3)),
    "skip_chance": float(E("SKIP_CHANCE", 0.2)), # random skipped runs, looks natural
    "title_sim": 0.7, "body_sim": 0.35, "attempts": 4,
}

# ================================================================ CONFIG (edit names, colors, seeds here)
CFG = json.loads(r'''
{
  "blogs": {
    "en": {
      "name": "Fixwise",
      "split": 3,
      "tagline": "Fix it fast. Understand why.",
      "sub": "Clear, step-by-step guides for phone and app problems: Android, iPhone, WhatsApp and more.",
      "blog_id_env": "BLOG_ID_EN",
      "site_env": "BLOG_URL_EN",
      "accent": "#0b7a75",
      "accent2": "#f2a900",
      "font": "Inter:wght@400;500;700;800",
      "font_family": "Inter",
      "hl": "en",
      "gl": ["us", "gb", "au", "ca"],
      "trend_geos": ["US", "GB", "AU", "CA"],
      "letters": "abcdefghijklmnoprstuw",
      "lang_name": "English (US spelling)",
      "market": "readers in the US, UK, Canada and Australia; avoid carrier-, bank- or country-specific advice",
      "faq": "Frequently asked questions",
      "toc": "In this guide",
      "min": "min read",
      "updated": "Last reviewed",
      "related": "Related guides",
      "flow": "Quick fix path",
      "styles": [
        "step-by-step troubleshooting guide, ordered from the quickest fix to the most thorough one",
        "diagnosis guide: short if/then checks that help the reader find which cause applies, then the fix for each cause",
        "explainer: why this happens in plain language, then the fixes, then how to prevent it",
        "checklist-style guide with a clear 'when to stop and contact support' section"
      ]
    },
    "ar": {
      "name": "حلّها",
      "tagline": "حلّها بسرعة وافهم السبب",
      "sub": "شروحات واضحة وخطوات مرتبة لمشاكل الجوال والتطبيقات: أندرويد، آيفون، واتساب وغيرها.",
      "blog_id_env": "BLOG_ID_AR",
      "site_env": "BLOG_URL_AR",
      "accent": "#0f6b5c",
      "accent2": "#d99a00",
      "font": "IBM+Plex+Sans+Arabic:wght@400;500;700",
      "font_family": "IBM Plex Sans Arabic",
      "hl": "ar",
      "gl": ["sa", "ae", "kw", "qa"],
      "trend_geos": ["SA", "AE", "KW", "QA"],
      "letters": "ابتجحخدرسشصعفقكلمنهوي",
      "lang_name": "Arabic (clear modern standard Arabic with simple wording, no diacritics/tashkeel)",
      "market": "Arabic-speaking readers, mainly in Saudi Arabia, the UAE, Kuwait, Qatar, Bahrain and Oman",
      "faq": "أسئلة شائعة",
      "toc": "في هذا الدليل",
      "min": "دقائق قراءة",
      "updated": "آخر مراجعة",
      "related": "أدلة ذات صلة",
      "flow": "مسار الحل السريع",
      "styles": [
        "step-by-step troubleshooting guide, ordered from the quickest fix to the most thorough one",
        "diagnosis guide: short if/then checks that help the reader find which cause applies, then the fix for each cause",
        "explainer: why this happens in plain language, then the fixes, then how to prevent it",
        "checklist-style guide with a clear 'when to stop and contact support' section"
      ]
    }
  },
  "categories": {
    "en": {
      "Android": ["android phone keeps", "android not", "samsung galaxy keeps", "google play store not"],
      "iPhone": ["iphone keeps", "iphone not", "ios update", "iphone won't"],
      "WhatsApp": ["whatsapp not", "whatsapp won't", "whatsapp keeps", "whatsapp backup"],
      "Social Apps": ["instagram not", "snapchat not", "tiktok not", "telegram not"],
      "Storage & Speed": ["phone storage full", "phone slow after", "phone lagging", "clear cache"],
      "Battery & Charging": ["phone battery drains", "phone not charging", "iphone battery", "android battery drain"],
      "Wi-Fi & Network": ["phone wifi not", "mobile data not", "bluetooth won't", "hotspot not working"],
      "Security & Privacy": ["phone hacked", "remove virus android", "privacy settings phone", "two-factor authentication"]
    },
    "ar": {
      "أندرويد": ["جوال اندرويد لا", "سامسونج لا يعمل", "متجر بلاي لا", "اندرويد بطيء"],
      "آيفون": ["ايفون لا", "ايفون يفصل", "تحديث ايفون", "ايفون بطيء"],
      "واتساب": ["واتساب لا", "واتساب ما", "واتساب النسخ الاحتياطي", "واتساب رمز التفعيل"],
      "تطبيقات التواصل": ["سناب شات لا", "انستقرام لا", "تيك توك لا", "تليجرام لا"],
      "التخزين والأداء": ["مساحة التخزين ممتلئة", "الجوال بطيء", "تهنيج الجوال", "مسح الكاش"],
      "البطارية والشحن": ["بطارية الجوال تخلص", "الجوال لا يشحن", "بطارية ايفون", "استهلاك البطارية"],
      "الواي فاي والشبكة": ["الواي فاي لا", "بيانات الجوال لا", "البلوتوث لا", "الهوتسبوت لا"],
      "الأمان والخصوصية": ["اختراق الجوال", "حذف فيروس اندرويد", "اعدادات الخصوصية", "التحقق بخطوتين"]
    }
  }
}
''')

# ================================================================ SITE: pages + theme
PAGES = {
"en": [
("About", """<p>__NAME__ publishes practical guides for fixing common problems with smartphones and mobile apps: Android, iPhone, WhatsApp, social apps, storage, battery and connectivity.</p>
<h2>What you will find here</h2>
<p>Step-by-step fixes in plain language, ordered from the quickest solution to the most thorough one, with notes on where steps can look different depending on your device or app version.</p>
<h2>How we work</h2>
<p>Each guide covers one specific problem people run into. Steps are based on how the device or app maker documents its own features. Articles go through a review step before publishing and are revisited when software changes. Our Editorial Policy explains the details.</p>
<h2>Contact</h2>
<p>Found a mistake or have a suggestion? Email <a href="mailto:__EMAIL__">__EMAIL__</a>.</p>"""),
("Contact", """<p>We read every message about corrections, questions and suggestions.</p>
<p><strong>Email:</strong> <a href="mailto:__EMAIL__">__EMAIL__</a></p>
<p>When you report a problem with a guide, please include the article title, your device model and software version, and what you saw. This helps us fix it faster.</p>
<p>We usually reply within a few business days.</p>"""),
("Privacy Policy", """<p>This policy explains what information is collected when you visit __NAME__ and how it is used.</p>
<h2>Information we collect</h2>
<p>We do not ask visitors to create accounts. Like most websites, our hosting platform and analytics tools automatically record technical data such as IP address, browser type, device type, pages viewed and time of visit. If you email us, we keep your message and address only to respond.</p>
<h2>Cookies</h2>
<p>Cookies are small files stored in your browser. We and our partners use them to keep the site working, understand how it is used, and show advertising.</p>
<h2>Advertising</h2>
<p>We use Google AdSense to display ads. Google and its partners use cookies to serve ads based on your visits to this and other websites. You can manage personalized advertising in <a href="https://adssettings.google.com" rel="noopener">Google Ads Settings</a> or learn more at <a href="https://policies.google.com/technologies/ads" rel="noopener">Google's advertising policies</a>. You can also opt out of some third-party vendors' use of cookies at <a href="https://www.aboutads.info" rel="noopener">aboutads.info</a>.</p>
<h2>Third-party links</h2>
<p>Our articles may link to websites we do not control. We are not responsible for their content or privacy practices.</p>
<h2>Children</h2>
<p>This site is not directed at children under 13 and we do not knowingly collect their personal information.</p>
<h2>Your rights</h2>
<p>Depending on where you live (for example the EEA, UK or California), you may have rights to access, correct or delete your personal data. Contact us at <a href="mailto:__EMAIL__">__EMAIL__</a> to make a request.</p>
<h2>Changes</h2>
<p>We may update this policy from time to time. The latest version is always on this page.</p>"""),
("Terms of Use", """<p>By using __NAME__ you agree to these terms.</p>
<h2>Use of content</h2>
<p>Content is provided for personal, non-commercial information. You may link to our articles. You may not copy, republish or resell them without permission.</p>
<h2>No warranty</h2>
<p>Guides are provided as is. Devices, apps and software versions differ, and we cannot guarantee that a given fix will work for you.</p>
<h2>Limitation of liability</h2>
<p>To the extent permitted by law, __NAME__ is not liable for any loss or damage, including data loss, arising from use of this site or from following its instructions.</p>
<h2>External links</h2>
<p>We are not responsible for third-party websites we link to.</p>
<h2>Changes</h2>
<p>We may update these terms at any time. Continued use of the site means you accept the updated terms.</p>"""),
("Disclaimer", """<p>The information on __NAME__ is for general guidance only.</p>
<p>Menu names, settings and behavior vary by manufacturer, model, region and software version. Before you follow any guide, back up important data, especially before resetting a device or deleting app data.</p>
<p>__NAME__ is independent and is not affiliated with, endorsed by or sponsored by Apple, Google, Samsung, Meta, WhatsApp or any other company mentioned. All trademarks belong to their respective owners.</p>
<p>If a problem persists, contact the device or app maker's official support or a qualified technician.</p>"""),
("Editorial Policy", """<h2>Our goal</h2>
<p>Every guide should help a reader solve a specific problem clearly and safely.</p>
<h2>How articles are made</h2>
<p>Articles are prepared with the help of automated writing tools and go through an automated accuracy review before publishing. Topics are chosen from questions people actually search for.</p>
<h2>Accuracy</h2>
<p>We aim to describe only steps and settings that exist, and to say so when a step differs between devices or versions. We do not invent statistics, quotes or test results.</p>
<h2>Updates and corrections</h2>
<p>Each article shows a review date. If you find an error, email <a href="mailto:__EMAIL__">__EMAIL__</a> and we will correct it.</p>
<h2>Advertising</h2>
<p>Ads on this site do not influence what we write.</p>"""),
],
"ar": [
("من نحن", """<p>يقدم __NAME__ شروحات عملية لحل المشاكل الشائعة في الجوالات والتطبيقات: أندرويد، آيفون، واتساب، تطبيقات التواصل، التخزين، البطارية والاتصال بالشبكة.</p>
<h2>ماذا تجد هنا</h2>
<p>خطوات مرتبة بلغة بسيطة، من الحل الأسرع إلى الأشمل، مع تنبيه عندما تختلف الخطوات حسب نوع الجهاز أو إصدار التطبيق.</p>
<h2>كيف نعمل</h2>
<p>كل دليل يعالج مشكلة محددة يواجهها الناس. نعتمد في الخطوات على ما توثقه الشركة المصنعة للجهاز أو التطبيق. تمر المقالات بمرحلة مراجعة قبل النشر، ونعيد النظر فيها عند تغير البرامج. تجد التفاصيل في صفحة السياسة التحريرية.</p>
<h2>تواصل معنا</h2>
<p>وجدت خطأ أو عندك اقتراح؟ راسلنا على <a href="mailto:__EMAIL__">__EMAIL__</a>.</p>"""),
("اتصل بنا", """<p>نقرأ كل رسالة تخص التصحيحات والأسئلة والاقتراحات.</p>
<p><strong>البريد الإلكتروني:</strong> <a href="mailto:__EMAIL__">__EMAIL__</a></p>
<p>عند الإبلاغ عن مشكلة في دليل معين، اذكر عنوان المقال وموديل جهازك وإصدار النظام وماذا ظهر لك. هذا يساعدنا في التصحيح بشكل أسرع.</p>
<p>نرد عادة خلال أيام عمل قليلة.</p>"""),
("سياسة الخصوصية", """<p>توضح هذه السياسة المعلومات التي تُجمع عند زيارتك __NAME__ وكيف تُستخدم.</p>
<h2>المعلومات التي نجمعها</h2>
<p>لا نطلب من الزوار إنشاء حسابات. مثل أغلب المواقع، تسجل منصة الاستضافة وأدوات التحليل تلقائيا بيانات تقنية مثل عنوان IP ونوع المتصفح ونوع الجهاز والصفحات المعروضة ووقت الزيارة. إذا راسلتنا بالبريد نحتفظ برسالتك وعنوانك فقط للرد عليك.</p>
<h2>ملفات تعريف الارتباط (الكوكيز)</h2>
<p>الكوكيز ملفات صغيرة تُخزن في متصفحك. نستخدمها نحن وشركاؤنا لتشغيل الموقع وفهم طريقة استخدامه وعرض الإعلانات.</p>
<h2>الإعلانات</h2>
<p>نستخدم Google AdSense لعرض الإعلانات. تستخدم Google وشركاؤها الكوكيز لعرض إعلانات بناء على زياراتك لهذا الموقع ومواقع أخرى. يمكنك إدارة الإعلانات المخصصة من <a href="https://adssettings.google.com" rel="noopener">إعدادات إعلانات Google</a> أو معرفة المزيد من <a href="https://policies.google.com/technologies/ads" rel="noopener">سياسات الإعلانات في Google</a>. ويمكنك أيضا إيقاف استخدام بعض الأطراف الخارجية للكوكيز من <a href="https://www.aboutads.info" rel="noopener">aboutads.info</a>.</p>
<h2>روابط خارجية</h2>
<p>قد تحتوي مقالاتنا على روابط لمواقع لا نتحكم بها، ولسنا مسؤولين عن محتواها أو سياسات الخصوصية فيها.</p>
<h2>الأطفال</h2>
<p>الموقع غير موجه للأطفال دون 13 سنة، ولا نجمع عمدا معلومات شخصية عنهم.</p>
<h2>حقوقك</h2>
<p>حسب بلد إقامتك قد يكون لك الحق في الوصول إلى بياناتك أو تصحيحها أو حذفها. راسلنا على <a href="mailto:__EMAIL__">__EMAIL__</a> لتقديم طلبك.</p>
<h2>التعديلات</h2>
<p>قد نحدّث هذه السياسة من وقت لآخر، وتجد أحدث نسخة دائما في هذه الصفحة.</p>"""),
("شروط الاستخدام", """<p>باستخدامك __NAME__ فأنت توافق على هذه الشروط.</p>
<h2>استخدام المحتوى</h2>
<p>المحتوى مقدم للمعرفة الشخصية وغير التجارية. يمكنك وضع روابط لمقالاتنا، ولا يجوز نسخها أو إعادة نشرها أو بيعها دون إذن.</p>
<h2>عدم الضمان</h2>
<p>الشروحات مقدمة كما هي. تختلف الأجهزة والتطبيقات وإصدارات النظام، ولا نضمن نجاح حل معين على جهازك.</p>
<h2>حدود المسؤولية</h2>
<p>بالقدر الذي يسمح به القانون، لا يتحمل __NAME__ مسؤولية أي خسارة أو ضرر، بما فيها فقدان البيانات، ناتج عن استخدام الموقع أو اتباع تعليماته.</p>
<h2>الروابط الخارجية</h2>
<p>لسنا مسؤولين عن المواقع الخارجية التي نضع روابط لها.</p>
<h2>التعديلات</h2>
<p>قد نعدل هذه الشروط في أي وقت، واستمرارك في استخدام الموقع يعني قبولك للشروط المحدثة.</p>"""),
("إخلاء المسؤولية", """<p>المعلومات في __NAME__ للإرشاد العام فقط.</p>
<p>تختلف أسماء القوائم والإعدادات وسلوك النظام حسب الشركة المصنعة والموديل والمنطقة وإصدار النظام. قبل تنفيذ أي دليل، انسخ بياناتك المهمة احتياطيا، خصوصا قبل إعادة ضبط الجهاز أو حذف بيانات تطبيق.</p>
<p>__NAME__ موقع مستقل وغير مرتبط أو معتمد أو مدعوم من Apple أو Google أو Samsung أو Meta أو واتساب أو أي شركة أخرى مذكورة، وجميع العلامات التجارية ملك لأصحابها.</p>
<p>إذا استمرت المشكلة، تواصل مع الدعم الرسمي لصانع الجهاز أو التطبيق أو مع فني مختص.</p>"""),
("السياسة التحريرية", """<h2>هدفنا</h2>
<p>أن يساعد كل دليل القارئ على حل مشكلة محددة بوضوح وأمان.</p>
<h2>كيف تُعد المقالات</h2>
<p>تُعد المقالات بمساعدة أدوات كتابة آلية، وتمر بمراجعة آلية للدقة قبل النشر. نختار المواضيع من أسئلة يبحث عنها الناس فعلا.</p>
<h2>الدقة</h2>
<p>نحرص على ذكر خطوات وإعدادات موجودة فعلا، وننبه عندما تختلف الخطوات بين الأجهزة أو الإصدارات. لا نختلق إحصائيات أو اقتباسات أو نتائج اختبارات.</p>
<h2>التحديث والتصحيح</h2>
<p>يظهر في كل مقال تاريخ المراجعة. إذا وجدت خطأ راسلنا على <a href="mailto:__EMAIL__">__EMAIL__</a> وسنصححه.</p>
<h2>الإعلانات</h2>
<p>الإعلانات في الموقع لا تؤثر على ما نكتبه.</p>"""),
],
}


def logo_svg(a, b):
    """Brand mark: a phone with a check mark (problem fixed)."""
    return (f"<svg viewBox='0 0 48 48' xmlns='http://www.w3.org/2000/svg'>"
            f"<rect width='48' height='48' rx='13' fill='{a}'/>"
            f"<rect x='15' y='8' width='18' height='32' rx='4.5' fill='none' stroke='#ffffff' stroke-width='3'/>"
            f"<path d='M19.8 25l3.4 3.5L29.6 20' fill='none' stroke='{b}' stroke-width='3.6' stroke-linecap='round' stroke-linejoin='round'/>"
            f"<circle cx='24' cy='35.4' r='1.4' fill='#ffffff'/></svg>")


THEME = r"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE html>
<html b:responsive='true' b:version='2' class='v2' expr:dir='data:blog.languageDirection' expr:lang='data:blog.locale' xmlns='http://www.w3.org/1999/xhtml' xmlns:b='http://www.google.com/2005/gml/b' xmlns:data='http://www.google.com/2005/gml/data' xmlns:expr='http://www.google.com/2005/gml/expr'>
<head>
<meta content='width=device-width, initial-scale=1' name='viewport'/>
<meta content='__ACC__' name='theme-color'/>
<title><data:blog.pageTitle/></title>
<b:include data='blog' name='all-head-content'/>
<link href='https://fonts.googleapis.com/css2?family=__FONTQ__&amp;display=swap' rel='stylesheet'/>
<link href='data:image/svg+xml;base64,__FAVICON__' rel='icon' type='image/svg+xml'/>
<b:skin><![CDATA[
:root{--bg:#f3f5f6;--card:#ffffff;--ink:#14202b;--mute:#5d6b78;--line:#dde3e8;--acc:__ACC__;--hi:__ACC2__;--r:12px;
--f:'__FF__',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
@media(prefers-color-scheme:dark){:root{--bg:#0f161c;--card:#172029;--ink:#e6ecf1;--mute:#93a1ad;--line:#26323d;--acc:color-mix(in srgb,__ACC__ 55%,#fff)}}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.75 var(--f);-webkit-font-smoothing:antialiased}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:3px solid var(--hi);outline-offset:2px}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px}
#navbar,.Navbar{display:none}
#progress{position:fixed;top:0;inset-inline-start:0;height:3px;width:0;background:var(--hi);z-index:99}
.site-header{position:sticky;top:0;z-index:50;background:var(--card);border-bottom:1px solid var(--line)}
.bar{display:flex;align-items:center;gap:20px;min-height:62px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:20px;color:var(--ink);letter-spacing:-.01em;flex:none}
.brand:hover{text-decoration:none}
.brand svg{width:36px;height:36px}
.brand b{color:var(--acc);font-weight:800}
.nav{flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.nav h3,.nav .title,.label-count{display:none}
.nav ul{list-style:none;margin:0;padding:0;display:flex;gap:4px;white-space:nowrap}
.nav li{margin:0;padding:0;border:0}
.nav li a{display:block;padding:6px 12px;border-radius:8px;font-size:14px;font-weight:600;color:var(--mute)}
.nav li a:hover{background:var(--bg);color:var(--ink);text-decoration:none}
.hero{padding:48px 0 10px;max-width:720px}
.hero h1{font-size:clamp(30px,5.4vw,46px);line-height:1.12;margin:0 0 12px;letter-spacing:-.025em;font-weight:800}
.hero p{margin:0;color:var(--mute);font-size:18px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:34px;padding-block:30px 20px;align-items:start}
@media(max-width:900px){.layout{grid-template-columns:1fr}}
.post-outer{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:24px 26px;margin-bottom:16px}
.index .post-outer:hover{border-color:var(--acc)}
.single .post-outer{padding:34px clamp(20px,4vw,42px)}
.date-header{margin:0 0 6px;font-size:13px;font-weight:600;color:var(--mute)}
.date-header span{background:none;color:inherit;margin:0;padding:0;letter-spacing:0;font-weight:inherit}
.post-title{margin:0 0 12px;font-size:23px;line-height:1.3;letter-spacing:-.015em;font-weight:800}
.single .post-title{font-size:clamp(28px,4.4vw,40px);line-height:1.18;letter-spacing:-.025em}
.post-title a{color:var(--ink)}
.post-body{font-size:18px;line-height:1.85;overflow-wrap:break-word}
.index .post-body{font-size:16px;line-height:1.7;color:var(--mute)}
.single .post-body{max-width:70ch}
.post-body h2{font-size:25px;line-height:1.3;margin:2em 0 .6em;padding-inline-start:14px;border-inline-start:4px solid var(--acc);letter-spacing:-.01em}
.post-body h3{font-size:19px;line-height:1.4;margin:1.6em 0 .35em}
.post-body p{margin:0 0 1.1em}
.post-body ol,.post-body ul{padding-inline-start:1.4em;margin:0 0 1.2em}
.post-body li{margin:.45em 0}
.post-body li::marker{color:var(--acc);font-weight:700}
.post-body p.lead{font-size:19px;font-weight:500;color:var(--ink);background:var(--bg);border-inline-start:4px solid var(--hi);padding:14px 18px;border-radius:8px}
.index .post-body p.lead{font-size:16px;font-weight:400;color:var(--mute);background:none;border:0;padding:0}
.note,.warn{padding:13px 18px;border-radius:10px;margin:1.3em 0;font-size:16px;line-height:1.7}
.note{background:color-mix(in srgb,var(--acc) 9%,var(--card));border:1px solid color-mix(in srgb,var(--acc) 35%,transparent)}
.warn{background:color-mix(in srgb,var(--hi) 14%,var(--card));border:1px solid color-mix(in srgb,var(--hi) 55%,transparent)}
.post-body table{display:block;overflow-x:auto;border-collapse:collapse;width:100%;font-size:16px;margin:1.2em 0}
.post-body td,.post-body th{padding:10px 12px;border:1px solid var(--line);text-align:start;vertical-align:top}
.post-body th{background:var(--bg)}
.post-body img{max-width:100%;height:auto;border-radius:8px}
.toc{border:1px solid var(--line);border-radius:10px;padding:14px 20px;margin:1.4em 0;font-size:16px;line-height:1.6}
.toc ol{margin:8px 0 0;padding-inline-start:1.3em}
.toc li{margin:.3em 0}
.toc a{color:var(--ink)}
.updated{color:var(--mute);font-size:14px;margin-top:2em;border-top:1px solid var(--line);padding-top:12px}
.jump-link a{display:inline-block;margin-top:6px;font-weight:700;font-size:14px}
.post-footer{margin-top:16px}
.post-footer-line-1,.post-footer-line-3,.post-share-buttons,.post-icons,.post-author,.post-timestamp,.post-comment-link,.post-location{display:none}
.post-labels{font-size:0}
.post-labels a{display:inline-block;font-size:13px;font-weight:700;padding:3px 12px;border-radius:99px;background:color-mix(in srgb,var(--acc) 11%,transparent);margin-inline-end:6px}
.post-labels a:hover{text-decoration:none;background:color-mix(in srgb,var(--acc) 20%,transparent)}
#blog-pager{display:flex;justify-content:space-between;gap:12px;margin:20px 0;overflow:hidden}
#blog-pager a{float:none;padding:9px 16px;border:1px solid var(--line);border-radius:10px;background:var(--card);font-weight:600;font-size:14px}
.home-link{display:none}
.sidebar .widget{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:18px 20px;margin-bottom:16px}
.sidebar h3{margin:0 0 10px;font-size:15px;font-weight:800}
.sidebar ul{list-style:none;margin:0;padding:0}
.sidebar li{padding:8px 0;border-bottom:1px solid var(--line);font-size:15px;line-height:1.5}
.sidebar li:last-child{border:0}
.sidebar .item-title a{color:var(--ink);font-weight:600}
.sidebar img{border-radius:8px}
.BlogSearch input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit;font-size:15px}
.BlogSearch input[type=submit],.BlogSearch button{margin-top:8px;padding:8px 16px;border:0;border-radius:8px;background:var(--acc);color:#fff;font:inherit;font-weight:700;cursor:pointer}
.BlogSearch table,.BlogSearch td{display:block;width:100%;padding:0;border:0}
.site-footer{margin-top:20px;border-top:1px solid var(--line);background:var(--card);padding:28px 0;color:var(--mute);font-size:14px}
.flinks{display:flex;flex-wrap:wrap;gap:6px 22px;margin-bottom:12px}
.flinks a{color:var(--ink);font-weight:600}
.site-footer .widget{margin:8px 0 0}
.sidebar .widget{display:none}
.sidebar .widget.BlogSearch,.sidebar .widget.PopularPosts{display:block}
.static_page .post-outer{padding:30px clamp(20px,4vw,40px)}
html[dir=rtl] body{line-height:1.95}
html[dir=rtl] .post-title,html[dir=rtl] .hero h1{letter-spacing:0;line-height:1.4}
]]></b:skin>
</head>
<body expr:class='data:view.isSingleItem ? "single" : "index"'>
<div id='progress'></div>
<header class='site-header'><div class='wrap bar'>
<a class='brand' expr:href='data:blog.homepageUrl'>__LOGO__<span>__BRAND__</span></a>
<b:section class='nav' id='nav' maxwidgets='1' showaddelement='no'>
<b:widget id='Label1' locked='false' title='Topics' type='Label' version='2'/>
</b:section>
</div></header>
<div class='wrap'>
<b:if cond='data:view.isHomepage'>
<section class='hero'><h1>__TAGLINE__</h1><p>__SUB__</p></section>
</b:if>
<div class='layout'>
<main class='content'>
<b:section class='main' id='main' showaddelement='no'>
<b:widget id='Blog1' locked='true' title='Blog Posts' type='Blog' version='2'/>
</b:section>
</main>
<aside class='sidebar'>
<b:section class='sidebar' id='sidebar' preferred='yes'>
<b:widget id='BlogSearch1' locked='false' title='__SEARCH__' type='BlogSearch' version='2'/>
<b:widget id='PopularPosts1' locked='false' title='__POPULAR__' type='PopularPosts' version='2'/>
</b:section>
</aside>
</div>
</div>
<footer class='site-footer'><div class='wrap'>
<nav class='flinks'>__FLINKS__</nav>
<div>&#169; <span id='yr'></span> __NAME__</div>
<b:section class='fsec' id='footer' maxwidgets='1' showaddelement='no'>
<b:widget id='Attribution1' locked='true' title='' type='Attribution' version='2'/>
</b:section>
</div></footer>
<script type='text/javascript'>
//<![CDATA[
(function(){
var y=document.getElementById('yr'); if(y){y.textContent=new Date().getFullYear();}
var bar=document.getElementById('progress');
var b=document.querySelector('.single .post-body');
if(b){
  var hs=b.querySelectorAll('h2');
  if(hs.length>=3){
    var t=document.createElement('nav'); t.className='toc';
    var s=document.createElement('strong'); s.textContent=__TOC__; t.appendChild(s);
    var ol=document.createElement('ol');
    for(var i=0;i<hs.length;i++){
      hs[i].id='s'+i;
      var li=document.createElement('li'), a=document.createElement('a');
      a.href='#s'+i; a.textContent=hs[i].textContent; li.appendChild(a); ol.appendChild(li);
    }
    t.appendChild(ol);
    var lead=b.querySelector('p.lead');
    if(lead&&lead.parentNode){lead.parentNode.insertBefore(t,lead.nextSibling);}else{b.insertBefore(t,b.firstChild);}
  }
  var words=(b.textContent||'').trim().split(/\s+/).length;
  var h=document.querySelector('.single .post-title');
  if(h){var m=document.createElement('div'); m.className='date-header'; m.textContent=Math.max(1,Math.round(words/200))+' '+__MIN__; h.parentNode.insertBefore(m,h.nextSibling);}
  window.addEventListener('scroll',function(){
    var d=document.documentElement; var r=d.scrollHeight-d.clientHeight;
    if(r>0&&bar){bar.style.width=(d.scrollTop/r*100)+'%';}
  },{passive:true});
}
})();
//]]>
</script>
</body>
</html>
"""


def build_theme(cfg, lang, page_links):
    c = cfg["blogs"][lang]
    logo = logo_svg(c["accent"], c["accent2"])
    n = c.get("split")
    brand = (html.escape(c["name"][:n]) + "<b>" + html.escape(c["name"][n:]) + "</b>") if n else html.escape(c["name"])
    fav = base64.b64encode(logo.encode()).decode()
    flinks = "".join(f"<a href='{html.escape(u, quote=True)}'>{html.escape(t)}</a>" for t, u in page_links.items())
    ar = lang == "ar"
    rep = {
        "__ACC2__": c["accent2"], "__ACC__": c["accent"], "__FONTQ__": c["font"], "__FF__": c["font_family"],
        "__FAVICON__": fav, "__LOGO__": logo, "__BRAND__": brand, "__NAME__": html.escape(c["name"]),
        "__TAGLINE__": html.escape(c["tagline"]), "__SUB__": html.escape(c["sub"]),
        "__SEARCH__": "بحث" if ar else "Search", "__POPULAR__": "الأكثر قراءة" if ar else "Popular guides",
        "__FLINKS__": flinks, "__TOC__": json.dumps(c["toc"], ensure_ascii=False),
        "__MIN__": json.dumps(c["min"], ensure_ascii=False),
    }
    out = THEME
    for k, v in rep.items():
        out = out.replace(k, v)
    return out

# ================================================================ BOT
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}

# ---------------------------------------------------------------- helpers
def http():
    import requests
    return requests

_tok = {}
def token():
    if "t" not in _tok:
        r = http().post("https://oauth2.googleapis.com/token", timeout=30, data={
            "client_id": os.environ["G_CLIENT_ID"], "client_secret": os.environ["G_CLIENT_SECRET"],
            "refresh_token": os.environ["G_REFRESH_TOKEN"], "grant_type": "refresh_token"})
        r.raise_for_status()
        _tok["t"] = r.json()["access_token"]
    return _tok["t"]

def auth():
    return {"Authorization": "Bearer " + token()}

def load(lang):
    p = DATA / f"state_{lang}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"posts": [], "pages": {}}

def save(lang, st):
    DATA.mkdir(exist_ok=True)
    (DATA / f"state_{lang}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

def norm(s):
    return re.findall(r"\w+", s.lower())

def jacc(a, b):
    a, b = set(a), set(b)
    return len(a & b) / max(1, len(a | b))

def words(html_text):
    return len(re.sub(r"<[^>]+>", " ", html_text).split())

def sketch(text, k=5, n=128):
    w = norm(re.sub(r"<[^>]+>", " ", text))
    hs = {int(hashlib.md5(" ".join(w[i:i + k]).encode()).hexdigest()[:12], 16) for i in range(max(1, len(w) - k + 1))}
    return sorted(hs)[:n]

# ---------------------------------------------------------------- Gemini
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_gm = {"cands": None}

def _llm_state():
    p = DATA / "llm.json"
    return json.loads(p.read_text()) if p.exists() else {}

def _remember_model(name):
    st = _llm_state()
    if st.get("gemini_model") != name:
        DATA.mkdir(exist_ok=True)
        (DATA / "llm.json").write_text(json.dumps({"gemini_model": name}))

def gemini_candidates():
    """Every Flash model this key can see, newest first, with the last working one in front."""
    if _gm["cands"] is not None:
        return _gm["cands"]
    found = []
    try:
        r = http().get(GEMINI_BASE + "/models", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                       params={"pageSize": 200}, timeout=30)
        for m in r.json().get("models", []):
            n = m["name"].split("/")[-1]
            if "generateContent" in m.get("supportedGenerationMethods", []) and re.fullmatch(r"gemini-[\d.]+-flash(-lite)?", n):
                found.append((n, int(m.get("outputTokenLimit") or 8192)))
    except Exception as e:
        print("model listing failed:", str(e)[:120])
    if not found:   # listing unavailable: well-known names
        found = [(n, 32000) for n in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                                       "gemini-2.5-flash", "gemini-2.5-flash-lite")]
    found.sort(key=lambda x: (-float(re.search(r"gemini-([\d.]+)-", x[0]).group(1)), "lite" in x[0]))
    found += [("gemini-flash-latest", 32000), ("gemini-flash-lite-latest", 32000)]
    limits = dict(found)
    first = [n for n in (MODEL, _llm_state().get("gemini_model")) if n]
    order = first + [n for n, _ in found if n not in first]
    _gm["cands"] = [(n, limits.get(n, 32000)) for n in dict.fromkeys(order)]
    return _gm["cands"]

def gemini(prompt, temperature=0.9, search=False):
    """Try Gemini models one by one until one answers (free-tier access differs per model/project).
    Gemini 3 models think a lot by default (slow); writing does not need it, so thinking level is 'low'."""
    tried = []
    for name, limit in gemini_candidates()[:3 if search else 10]:
        cfg = {"maxOutputTokens": min(32000, limit)}
        if name.startswith("gemini-2"):        # Gemini 3 models no longer take sampling params
            cfg["temperature"] = temperature
        else:
            cfg["thinkingConfig"] = {"thinkingLevel": E("GEMINI_THINKING", "low")}
        r, err = None, ""
        for _ in range(3):
            try:
                r = http().post(f"{GEMINI_BASE}/models/{name}:generateContent", timeout=600,
                                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                                json=dict({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": cfg},
                                          **({"tools": [{"google_search": {}}]} if search else {})))
            except Exception as e:             # timeout / connection problem: try again, then next model
                r, err = None, type(e).__name__
                time.sleep(5)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(10)
                continue
            if r.status_code == 400 and "thinkingConfig" in cfg and "thinking" in r.text.lower():
                cfg.pop("thinkingConfig")      # this model does not accept the thinking setting
                continue
            break
        if r is not None and r.status_code == 200:
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                if text.strip():
                    print("gemini model used:", name)
                    _gm["cands"] = [(name, limit)] + [c for c in _gm["cands"] if c[0] != name]
                    _remember_model(name)
                    return text
            except (KeyError, IndexError):
                pass
        msg = f"{name}: " + (err or f"{r.status_code} {r.text[:110]}".replace(chr(10), " "))
        print("gemini model failed ->", msg)
        tried.append(msg)
    raise RuntimeError("Gemini: no model worked. " + " | ".join(tried[-3:]))

# ---------------------------------------------------------------- LLM chain: GLM first, Gemini as backup
SYSTEM = ("You are a senior editor at a phone-and-app help site with years of hands-on support experience. "
          "You write like a knowledgeable person explaining to a friend: direct, specific, occasionally opinionated, "
          "never robotic. You never invent facts, numbers, quotes or personal experiences.")

def glm(prompt, temperature):
    r = http().post(E("GLM_BASE_URL", "https://api.z.ai/api/paas/v4").rstrip("/") + "/chat/completions", timeout=480,
                    headers={"Authorization": "Bearer " + os.environ["GLM_API_KEY"]},
                    json={"model": E("GLM_MODEL", "glm-5.3"),
                          "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                          "temperature": temperature, "max_tokens": 20000,
                          "thinking": {"type": E("GLM_THINKING", "enabled")}})
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"] or ""

_dead = set()

def llm(prompt, temperature=0.85):
    """Writer chain. Default: GLM first, Gemini backup (LLM_PRIMARY=gemini flips it).
    A provider that fails for good is skipped for the rest of the run."""
    order = ["gemini", "glm"] if E("LLM_PRIMARY", "glm").lower() == "gemini" else ["glm", "gemini"]
    errors = []
    for name in order:
        if name in _dead or not os.getenv("GLM_API_KEY" if name == "glm" else "GEMINI_API_KEY"):
            continue
        for _ in range(2 if name == "glm" else 1):
            try:
                t0 = time.time()
                out = glm(prompt, temperature) if name == "glm" else gemini(prompt, temperature)
                if out.strip():
                    print(f"{name} call took {int(time.time() - t0)}s")
                    return out
            except Exception as e:
                errors.append(f"{name}: {str(e)[:200]}")
                print(f"{name} failed:", str(e)[:200])
                time.sleep(5)
        _dead.add(name)
        print(f"{name} unavailable, trying next")
    raise RuntimeError("No working writer model. " + " | ".join(errors[-2:]) if errors else
                       "No API key set: add GEMINI_API_KEY and/or GLM_API_KEY in GitHub secrets")

def llm_search(prompt, temperature=0.3):
    """Gemini answering with live Google Search results. Returns None when unavailable (never blocks publishing)."""
    if "search" in _dead or not SET["grounding"] or not os.getenv("GEMINI_API_KEY"):
        return None
    try:
        t0 = time.time()
        out = gemini(prompt, temperature, search=True)
        print(f"grounded search call took {int(time.time() - t0)}s")
        return out if out.strip() else None
    except Exception as e:
        print("grounded search unavailable, continuing without it:", str(e)[:160])
        _dead.add("search")
        return None

# ---------------------------------------------------------------- trends radar (Google Trends "trending now", last 24h)
TREND_PROMPT = """Google's trending searches over the last 24 hours (approximate traffic and a related headline):
{lines}

Pick at most 2 that a phone-and-app troubleshooting blog can answer with a genuinely useful fix-it or how-to guide:
app or service outages and errors, OS or app update problems, new-feature how-tos for Android, iPhone, WhatsApp, Instagram, Snapchat, TikTok, Telegram, Wi-Fi, battery, storage or security.
Ignore sports, celebrities, politics, shopping and entertainment.
For each write the search query a reader would type, in {lang_name}, plus the category, chosen from: {cats}.
Output one per line exactly as: <number> | <query> | <category>
If nothing fits, output only: NONE"""

def fetch_trends(lang):
    out = []
    for geo in CFG["blogs"][lang]["trend_geos"]:
        try:
            r = http().get(f"https://trends.google.com/trending/rss?geo={geo}", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                continue
            for it in ET.fromstring(r.content).iter("item"):
                title, traffic, news = (it.findtext("title") or "").strip(), "", []
                for ch in it:
                    tag = ch.tag.split("}")[-1]
                    if tag == "approx_traffic":
                        traffic = (ch.text or "").strip()
                    elif tag == "news_item":
                        news += [(g.text or "").strip() for g in ch if g.tag.split("}")[-1] == "news_item_title"]
                if title:
                    out.append({"q": title, "geo": geo, "traffic": traffic, "news": news[:2]})
        except Exception as e:
            print(f"trends {geo} failed:", str(e)[:100])
    return out

def pick_trending(lang, st):
    """Returns (category, query, context) for a trending topic that fits the blog, or None."""
    c, cats = CFG["blogs"][lang], CFG["categories"][lang]
    used = [norm(p["keyword"]) for p in st["posts"]]
    trends = [t for t in fetch_trends(lang) if all(jacc(norm(t["q"]), u) < 0.6 for u in used)][:60]
    if not trends:
        return None
    lines = "\n".join(f'{i}. {t["q"]} [{t["geo"]}, {t["traffic"] or "?"}]' + (f' - {" / ".join(t["news"])}' if t["news"] else "")
                      for i, t in enumerate(trends, 1))
    try:
        raw = llm(TREND_PROMPT.format(lines=lines, lang_name=c["lang_name"], cats=", ".join(cats)), 0.2)
    except Exception as e:
        print("trend filter failed:", str(e)[:120])
        return None
    for line in raw.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) == 3 and parts[0].strip("., ").isdigit() and parts[2] in cats and len(parts[1].split()) >= 2:
            t = trends[int(parts[0].strip("., ")) - 1] if int(parts[0].strip("., ")) <= len(trends) else None
            ctx = f'{t["q"]} ({t["traffic"] or "n/a"} searches, {t["geo"]})' + (f' - {" / ".join(t["news"])}' if t and t["news"] else "") if t else parts[1]
            print("trending topic chosen:", parts[1], "|", ctx)
            return parts[2], parts[1], ctx
    print("no trending topic fits the blog right now")
    return None

# ---------------------------------------------------------------- topics
def suggestions(q, hl, gl):
    try:
        r = http().get("https://suggestqueries.google.com/complete/search", timeout=15,
                       params={"client": "firefox", "q": q, "hl": hl, "gl": gl})
        return [s for s in r.json()[1] if isinstance(s, str)]
    except Exception:
        return []

def pick_topic(lang, st):
    c, cats = CFG["blogs"][lang], CFG["categories"][lang]
    used = [norm(p["keyword"]) for p in st["posts"]] + [norm(p["title"]) for p in st["posts"]]
    counts = {k: sum(1 for p in st["posts"] if k in p.get("labels", [])) for k in cats}
    low = min(counts.values())
    cat = random.choice([k for k, v in counts.items() if v == low])
    cands = set()
    for seed in random.sample(cats[cat], min(3, len(cats[cat]))):
        gl = random.choice(c["gl"])
        for q in (seed, seed + " " + random.choice(c["letters"])):
            cands |= set(suggestions(q, c["hl"], gl))
    cands = [x for x in cands if len(x.split()) >= 3 and all(jacc(norm(x), u) < 0.6 for u in used)]
    if not cands:
        raw = llm(f"List 10 specific, real problems people search for in the category '{cat}' for phones/apps. "
                     f"Write each as a natural search query in {c['lang_name']}, one per line, no numbering.", 1.0)
        cands = [l.strip("-• ").strip() for l in raw.splitlines() if len(l.split()) >= 3]
        cands = [x for x in cands if all(jacc(norm(x), u) < 0.6 for u in used)]
    return (cat, random.choice(cands)) if cands else (cat, None)

# ---------------------------------------------------------------- article
BRIEF = """Plan an SEO article for a phone/app help blog. Article language: {lang_name}. Audience: {market}.
Main search query: "{kw}"   Category: {cat}
Related searches people type (real autocomplete data): {related}
{extra}

Think like an SEO strategist, then write the brief in English using exactly these headings:
WORTH WRITING: YES only if the query is a specific, answerable phone/app problem or question; otherwise NO and stop.
INTENT: what the searcher wants and the situation they are in (1-2 sentences).
PRIMARY KEYWORD: the exact phrase to use.
SECONDARY KEYWORDS: 5-8 phrases from the related searches or natural variations.
GAP: what typical top results for this query do badly or leave out (be concrete) and how this article will be more useful.
OUTLINE: 6-8 H2 headings phrased the way a reader would search them, each with one line on what it covers.
QUESTIONS: 4 real follow-up questions for the FAQ."""

PROMPT = """Write the article described by this brief, in {lang_name}, for {market}.

BRIEF
{brief}

LIVE WEB RESEARCH (facts found with Google Search; use them in your own words, and drop anything that contradicts official documentation):
{research}
{trend}

LENGTH: {wmin}-{wmax} words. Depth beats padding: if a section has nothing new to say, cut it.

SEO RULES
- Title: under 60 characters, primary keyword near the start, written for a human to click, no clickbait.
- Opening <p class="lead">: the first sentence (max 150 characters) answers the query directly and works as the search snippet, then 1-2 sentences of context. Use the primary keyword naturally here.
- Use the primary keyword in at least one <h2>; use secondary keywords naturally in headings and text. Never stuff.
- Follow the OUTLINE but improve the order if you see a better one. Make headings specific.
- Cover the GAP: include what competitors usually skip (why it happens, how to tell which cause applies, what not to do, how long each fix takes, what to try if nothing works, when it is hardware or account-side).
- Scannable: short paragraphs, numbered steps for procedures, <strong> for key terms and menu names, one <div class="note"> tip and, where relevant, one <div class="warn"> caution, at most one <table>.
- End with <h2>{faq}</h2> using the QUESTIONS as <h3> + <p>.

HUMAN VOICE
- Sound like a knowledgeable person talking to a friend: direct, concrete, some opinion ("skip this one unless..."). Vary sentence length. Use contractions where natural in English.
- Specific over generic: the exact setting, the exact message the reader sees, the realistic cause.
- Avoid stock phrases and filler: "in today's digital world", "whether you're", "it's important to note", "delve", "seamless", "game-changer", "ultimate guide", "comprehensive guide", "in conclusion"; in Arabic avoid "في عالمنا الرقمي" and "في الختام". No intro that restates the question, no closing paragraph that repeats the article, no emoji.
- Never invent statistics, studies, quotes, URLs, test results or personal experience (no "I tested", "we tried"). If you are not sure a menu or setting exists, describe the general approach instead of guessing.

OUTPUT: HTML fragments only (no markdown, no <h1>, no html/head/body). Exactly this format:
TITLE: <title>
ICON: <exactly one of: phone, wifi, battery, storage, shield, chat, bell, apps, sync, update, camera, sound>
LABEL: <2-5 words in the article language naming the problem, for an illustration banner>
STEPS: <3-5 very short labels (1-4 words each, article language) of the fix path in order, separated by |>
---BODY---
<html>"""

REVIEW = """You are a strict technical editor for a phone/app troubleshooting site. Below is an article in {lang_name}.
Fix factual errors, remove or generalize any step, menu name, setting or claim you are not confident really exists, cut filler and repetition, make sure the first sentence answers the query directly, and keep the HTML structure (lead paragraph, h2 sections, FAQ), the keywords and a similar length. Do not add facts you are not sure of.
{verify}Reply in exactly this format:
CONFIDENCE: <number 1-10: how sure you are that every step, menu name and setting in your corrected article is real and accurate for current Android, iOS and app versions; 10 = certain, 7 or less = you doubt parts of it>
---BODY---
<the corrected HTML fragment>

{body}"""

def clean(h):
    h = re.sub(r"^```[a-z]*\s*|\s*```$", "", h.strip())
    h = re.sub(r"<(script|iframe|style)[\s\S]*?</\1>", "", h, flags=re.I)
    h = re.sub(r"</?(html|head|body|h1)[^>]*>", "", h, flags=re.I)
    return h.strip()

RESEARCH = """Research this for a phone/app troubleshooting article. Topic: "{kw}". Context if any: {context}
Use Google Search. Reply in English, concisely, with exactly these headings:
TOP RESULTS: what the current top pages for this query cover, and what they get wrong or leave out.
VERIFIED FACTS: exact menu paths, setting names, versions, limits and dates confirmed by official support pages (Apple Support, Android/Google Help, Samsung, WhatsApp Help Center, the app's own help). Mark anything you could not confirm.
CURRENT STATUS: any known outage, bug or recent update behind this topic, with dates. Write NONE if nothing is confirmed."""

VERIFY_SEARCH = ("Use Google Search to check every menu path, setting name, version number and claim against official documentation "
                 "(Apple Support, Android/Google Help, Samsung, WhatsApp Help Center, the app's own help pages). "
                 "Delete or generalize anything you cannot confirm.\n")

def write_article(lang, cat, kw, related, context=""):
    c = CFG["blogs"][lang]
    research = llm_search(RESEARCH.format(kw=kw, context=context or "none")) or ""
    if context and not research:   # a trending topic needs live facts; without them skip it instead of risking invented news
        print("trending topic skipped: no live research available")
        return None
    extra = (f"TRENDING NOW (last 24h): {context}\n" if context else "") + (f"LIVE WEB RESEARCH:\n{research}\n" if research else "")
    brief = llm(BRIEF.format(lang_name=c["lang_name"], market=c["market"], kw=kw, cat=cat,
                             related="; ".join(related) or "none", extra=extra), 0.6)
    m = re.search(r"WORTH WRITING:\s*\**\s*(\w+)", brief, re.I)
    if m and m.group(1).upper().startswith("NO"):
        print("brief says skip:", kw)
        return None
    trend_note = ("This topic is trending right now. Only mention recent events if the research above confirms them; never invent news, dates or version numbers.\n"
                  if context else "")
    raw = llm(PROMPT.format(lang_name=c["lang_name"], market=c["market"], brief=brief, faq=c["faq"],
                            wmin=SET["words_min"], wmax=SET["words_max"],
                            research=research or "(not available - stick to what you are sure of)", trend=trend_note), 0.85)
    if "---BODY---" not in raw:
        return None
    head, body = raw.split("---BODY---", 1)
    m = re.search(r"TITLE:\s*(.+)", head)
    if not m:
        return None
    body = clean(body)
    if words(body) < SET["words_min"] * 0.65 or "<h2" not in body:
        print("too short or no headings:", words(body))
        return None
    conf = 10
    if SET["review"]:
        conf = 0   # unknown = not trusted
        try:
            out = llm_search(REVIEW.format(lang_name=c["lang_name"], body=body, verify=VERIFY_SEARCH), 0.2) if SET["grounding"] else None
            if not out:
                out = llm(REVIEW.format(lang_name=c["lang_name"], body=body, verify=""), 0.3)
            m2 = re.search(r"CONFIDENCE:\s*(\d+)", out)
            conf = int(m2.group(1)) if m2 else 0
            fixed = clean(out.split("---BODY---", 1)[1]) if "---BODY---" in out else ""
            if fixed and words(fixed) >= 0.8 * words(body) and "<h2" in fixed:
                body = fixed
            print("reviewer confidence:", conf)
        except Exception as e:
            print("review skipped:", e)
    title = m.group(1).strip().strip('"*#')
    if len(title) > 70:
        print("title too long:", title)
        return None

    def tag(name):
        mm = re.search(rf"^{name}:[ \t]*(.+)$", head, re.M)
        return mm.group(1).strip() if mm else ""
    steps = [x.strip(" -•*") for x in tag("STEPS").split("|") if x.strip(" -•*")][:5]
    return {"title": title, "body": body, "conf": conf, "icon": tag("ICON").lower(), "label": tag("LABEL"), "steps": steps}

def related_block(lang, st, cat, kw):
    n = SET["related_links"]
    pool = [p for p in st["posts"] if p.get("url") and not p.get("draft")]
    pool.sort(key=lambda p: (cat in p.get("labels", []), jacc(norm(kw), norm(p["keyword"]))), reverse=True)
    if n <= 0 or not pool:
        return ""
    items = "".join(f'<li><a href="{html.escape(p["url"], quote=True)}">{html.escape(p["title"])}</a></li>' for p in pool[:n])
    return f'\n<h2>{CFG["blogs"][lang]["related"]}</h2>\n<ul>{items}</ul>'

ICONS = {
    "phone": '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    "wifi": '<path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1"/>',
    "battery": '<rect x="2" y="7" width="18" height="10" rx="2"/><path d="M22 11v2"/><path d="M11 9l-2 3h3l-2 3"/>',
    "storage": '<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "apps": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "sync": '<path d="M20 11a8 8 0 0 0-14-4L4 9"/><path d="M4 4v5h5"/><path d="M4 13a8 8 0 0 0 14 4l2-2"/><path d="M20 20v-5h-5"/>',
    "update": '<circle cx="12" cy="12" r="9"/><path d="M12 7v7"/><path d="M8.5 11l3.5 3.5 3.5-3.5"/>',
    "camera": '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/><path d="M8 7l1.5-3h5L16 7"/>',
    "sound": '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9a4 4 0 0 1 0 6"/><path d="M19 6.5a8 8 0 0 1 0 11"/>',
}
CAT_ICON = {"Android": "phone", "iPhone": "phone", "WhatsApp": "chat", "Social Apps": "chat", "Storage & Speed": "storage",
            "Battery & Charging": "battery", "Wi-Fi & Network": "wifi", "Security & Privacy": "shield",
            "أندرويد": "phone", "آيفون": "phone", "واتساب": "chat", "تطبيقات التواصل": "chat", "التخزين والأداء": "storage",
            "البطارية والشحن": "battery", "الواي فاي والشبكة": "wifi", "الأمان والخصوصية": "shield"}

def hero_html(lang, art):
    """Branded banner at the top of the article: icon for the problem + short label."""
    c = CFG["blogs"][lang]
    icon = ICONS.get(art.get("icon")) or ICONS[CAT_ICON.get(art["cat"], "phone")]
    label = html.escape(art.get("label") or art["title"])
    cat = html.escape(art["cat"])
    return (f'<div style="display:flex;align-items:center;gap:16px;background:{c["accent"]};color:#fff;border-radius:14px;padding:16px 18px;margin:0 0 18px">'
            f'<svg viewBox="0 0 24 24" width="40" height="40" role="img" aria-label="{cat}" fill="none" stroke="#fff" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" style="flex:none;background:rgba(255,255,255,.16);border-radius:14px;padding:12px;box-sizing:content-box">{icon}</svg>'
            f'<div><div style="font-size:13px;font-weight:700;opacity:.8">{cat}</div>'
            f'<div style="font-size:20px;line-height:1.35;font-weight:700">{label}</div></div></div>')

def flow_html(lang, steps):
    """The fix path as numbered chips, shown right after the summary."""
    if len(steps) < 3:
        return ""
    c = CFG["blogs"][lang]
    arrow = "←" if lang == "ar" else "→"
    chips = [f'<span style="display:inline-flex;align-items:center;gap:8px;border:1px solid rgba(128,128,128,.45);border-radius:999px;'
             f'padding-block:6px;padding-inline:8px 14px;font-size:15px;font-weight:600">'
             f'<b style="display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;'
             f'background:{c["accent"]};color:#fff;font-size:13px">{i}</b>{html.escape(x)}</span>' for i, x in enumerate(steps, 1)]
    sep = f'<span aria-hidden="true" style="opacity:.55">{arrow}</span>'
    return (f'<div style="margin:0 0 22px"><div style="font-size:14px;font-weight:700;margin:0 0 8px;opacity:.75">{html.escape(c["flow"])}</div>'
            f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">{sep.join(chips)}</div></div>')

def finish(lang, art, st):
    c = CFG["blogs"][lang]
    d = dt.date.today()
    flow = flow_html(lang, art.get("steps", []))
    # banner + lead are the homepage card; the jump break hides the rest there
    body = hero_html(lang, art) + re.sub(r"(</p>)", lambda m: m.group(1) + "\n<!--more-->\n" + flow, art["body"], count=1)
    body += related_block(lang, st, art["cat"], art["kw"])
    body += f'\n<p class="updated">{c["updated"]}: {MONTHS[lang][d.month - 1]} {d.year}</p>'
    return body

# ---------------------------------------------------------------- Blogger
class Blogger:
    def __init__(self, blog_id):
        self.base = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}"

    def _send(self, path, payload, params=None):
        """POST with patient retries: Blogger answers 429 when a blog is new or busy."""
        for wait in (0, 60, 150, 300, 600):
            time.sleep(wait)
            r = http().post(self.base + path, headers=auth(), timeout=90, params=params, json=payload)
            if r.status_code != 429:
                if not r.ok:
                    print(f"BLOGGER ERROR {r.status_code} on {path}: {r.text[:600]}")
                r.raise_for_status()
                return r.json()
            print(f"429 from Blogger, waiting before retry (next wait {wait or 60}s+)...")
        r.raise_for_status()

    def post(self, title, html_body, labels, draft=False):
        return self._send("/posts", {"kind": "blogger#post", "title": title, "content": html_body, "labels": labels},
                          {"isDraft": str(draft).lower()})

    def page(self, title, html_body):
        return self._send("/pages", {"kind": "blogger#page", "title": title, "content": html_body})

    def pages(self):
        r = http().get(self.base + "/pages", headers=auth(), timeout=30, params={"fetchBodies": "false"})
        if not r.ok:
            print(f"BLOGGER ERROR {r.status_code} on /pages (read): {r.text[:600]}")
        r.raise_for_status()
        return r.json().get("items", [])

def submit_sitemap(site):
    """Tell Search Console about the sitemap (needs the Google account to own the property)."""
    site = site.rstrip("/") + "/"
    sm = site + "sitemap.xml"
    r = http().put("https://www.googleapis.com/webmasters/v3/sites/%s/sitemaps/%s" % (quote(site, safe=""), quote(sm, safe="")),
                   headers=auth(), timeout=30)
    print("sitemap submit:", r.status_code, "" if r.ok else r.text[:200])

def daily_cap(n):
    return 1 if n < SET["ramp1"] else 2 if n < SET["ramp2"] else SET["max_per_day"]

def run_post(lang):
    c = CFG["blogs"][lang]
    bid = os.getenv(c["blog_id_env"])
    if not bid:
        print(f"[{lang}] {c['blog_id_env']} not set, skipping")
        return
    st = load(lang)
    today = dt.date.today().isoformat()
    done = sum(1 for p in st["posts"] if p["date"] == today)
    if not FORCE and (done >= daily_cap(len(st["posts"])) or random.random() < SET["skip_chance"]):
        print(f"[{lang}] skipped this run (cap/spacing)")
        return
    art = None
    trend = pick_trending(lang, st) if SET["trends"] and random.random() < SET["trend_share"] else None
    for _ in range(SET["attempts"]):
        if trend:
            (cat, kw, context), trend = trend, None
        else:
            (cat, kw), context = pick_topic(lang, st), ""
        if not kw:
            continue
        related = [x for x in suggestions(kw, c["hl"], random.choice(c["gl"])) if x.lower() != kw.lower()][:8]
        a = write_article(lang, cat, kw, related, context)
        if not a:
            continue
        sk = sketch(a["body"])
        if any(jacc(norm(a["title"]), norm(p["title"])) > SET["title_sim"] for p in st["posts"]):
            print("similar title, retry")
            continue
        if any(len(set(sk) & set(p["sketch"])) / max(1, len(set(sk) | set(p["sketch"]))) > SET["body_sim"] for p in st["posts"]):
            print("similar body, retry")
            continue
        art = dict(a, cat=cat, kw=kw, sketch=sk)
        break
    if not art:
        print(f"[{lang}] no article produced")
        return
    as_draft = SET["draft"] or art["conf"] < SET["min_conf"]
    if as_draft and not SET["draft"]:
        print(f"[{lang}] reviewer confidence {art['conf']}/10 is below {SET['min_conf']}: saved as a draft for you to check")
    res = Blogger(bid).post(art["title"], finish(lang, art, st), [art["cat"]], draft=as_draft)
    st["posts"].append({"title": art["title"], "keyword": art["kw"], "labels": [art["cat"]],
                        "url": res.get("url"), "date": today, "sketch": art["sketch"], "draft": as_draft, "conf": art["conf"]})
    save(lang, st)
    print(f"[{lang}] {'drafted' if as_draft else 'published'}: {art['title']} -> {res.get('url')}")
    if not as_draft and os.getenv(c["site_env"]):
        submit_sitemap(os.environ[c["site_env"]])

# ---------------------------------------------------------------- setup / theme
def whoami(bid):
    """Print the blogs this Google account owns/writes to, so a wrong account or blog ID is obvious."""
    try:
        r = http().get("https://www.googleapis.com/blogger/v3/users/self/blogs", headers=auth(), timeout=30)
        items = r.json().get("items", []) if r.ok else []
        mine = [(b.get("id"), b.get("url")) for b in items]
        print("blogs this account can access:", mine if mine else f"none ({r.status_code} {r.text[:200]})")
        print("configured blog id is in that list:", any(i == bid for i, _ in mine))
    except Exception as e:
        print("whoami failed:", str(e)[:150])

def run_setup(lang):
    c = CFG["blogs"][lang]
    bid = os.getenv(c["blog_id_env"])
    if not bid:
        return
    st = load(lang)
    b = Blogger(bid)
    whoami(bid)
    live = {p["title"]: p.get("url") for p in b.pages()}
    email = os.environ.get("CONTACT_EMAIL", "contact@example.com")
    for title, body in PAGES[lang]:
        if title in live:
            st["pages"][title] = live[title]
            continue
        body = body.replace("__NAME__", c["name"]).replace("__EMAIL__", email)
        st["pages"][title] = b.page(title, body).get("url")
        save(lang, st)
        print(f"[{lang}] page created: {title}")
        time.sleep(20)
    save(lang, st)

def run_theme(lang):
    DIST.mkdir(exist_ok=True)
    (DIST / f"theme-{lang}.xml").write_text(build_theme(CFG, lang, load(lang)["pages"]), encoding="utf-8")
    print("theme written:", DIST / f"theme-{lang}.xml")



def cmd_token():
    import threading, webbrowser, urllib.parse, urllib.request
    from http.server import BaseHTTPRequestHandler, HTTPServer
    cid, sec = os.environ["G_CLIENT_ID"], os.environ["G_CLIENT_SECRET"]
    scopes = "https://www.googleapis.com/auth/blogger https://www.googleapis.com/auth/webmasters"
    redirect, got = "http://127.0.0.1:8765", {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = q.get("code", [""])[0]
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        def log_message(self, *a): pass

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": scopes,
        "access_type": "offline", "prompt": "consent"})
    print("Open this link if the browser does not open:\n", url)
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
    data = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": sec,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    res = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data)))
    print("\nG_REFRESH_TOKEN =", res["refresh_token"])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "post"
    if cmd == "token":
        cmd_token()
        sys.exit(0)
    langs = [sys.argv[2]] if len(sys.argv) > 2 else list(CFG["blogs"])
    failed = False
    for lg in langs:
        try:
            if cmd == "post":
                run_post(lg)
            elif cmd == "setup":
                run_setup(lg)
                run_theme(lg)
            elif cmd == "theme":
                run_theme(lg)
        except Exception as e:
            print(f"[{lg}] ERROR: {e}")
            if cmd != "post":
                raise
            failed = True
    if failed:
        sys.exit(1)  # makes the GitHub run turn red so you notice
