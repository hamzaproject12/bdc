import time
import json
import requests
import hashlib
import os
import math
import re
from functools import lru_cache
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
DATA_PATH = os.getenv("DATA_PATH", "data")
SEEN_FILE = os.path.join(DATA_PATH, "seen_offers.json")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- WHATSAPP CLOUD API ---
WA_TOKEN = os.getenv("WA_TOKEN")
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "1318151618051403")
WA_TEMPLATE = os.getenv("WA_TEMPLATE", "alerte_marche_public")
WA_LANG = os.getenv("WA_LANG", "fr")
WA_API_VERSION = os.getenv("WA_API_VERSION", "v25.0")
# Mettre WA_TEST=1 dans Railway pour envoyer un WhatsApp de controle au demarrage
WA_TEST = os.getenv("WA_TEST", "0") == "1"
# Mettre DEBUG_SCORING=1 pour voir dans les logs pourquoi chaque offre passe ou non
DEBUG_SCORING = os.getenv("DEBUG_SCORING", "0") == "1"

# --- ⏱️ RYTHME ---
SLEEP_OK = 14400       # 4h apres un scan reussi
SLEEP_FAIL = 900       # 15 min apres un echec

# --- 🎯 SEUILS ---
SEUIL_EVENT = 2        # Event & Formation : au moins 2 mots-cles (trop de bruit sinon)
SEUIL_DEFAUT = 1       # Les autres categories : 1 mot-cle suffit
# Les pepites passent TOUJOURS, meme avec un score de 0.

# --- 👥 CONFIGURATION DES ABONNÉS ---
# whatsapp : format international SANS "+" ni espaces (ex 212700301878)
# Le numero doit etre dans la liste des destinataires de test tant que
# l'app Meta est en mode developpement (5 numeros max).
# "Pépite" ajoute aux subscriptions = recoit aussi les pepites hors categorie.
SUBSCRIBERS = [
    {"name": "Moi", "id": "1952904877", "whatsapp": "212700301878", "subscriptions": ["ALL"]},
    #{"name": "Zakariya", "id": "8260779046", "whatsapp": "212660576019", "subscriptions": ["Event & Formation", "Pépite"]},
    {"name": "Hamza", "id": "8260779046", "whatsapp": "212665803935", "subscriptions": ["Event & Formation", "Pépite"]},
    # {"name": "Abdeslam", "id": "7943145340", "whatsapp": None, "subscriptions": ["Mdiq"]},
    # {"name": "Yassine", "id": "7879373928", "whatsapp": None, "subscriptions": ["Event & Formation"]},
]

# --- MOTS-CLÉS ---
KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app", "digital"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement", "ia"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique", "matériel informatique"],
    "Event & Formation": ["accompagnement","encadrement","formation", "atelier", "renforcement de capacité", "organisation", "animation", "sensibilisation", "impression", "conception", "enquête", "étude", "conseil agricole", "conseil", "agri"],
    "Mdiq": ["mdiq", "MDIQ-FNIDEQ", "MEDIAQ", "MDIQ FNIDEQ", "Sante", "GST"]
}

# --- 🔤 MOTS À MATCHER EXACTEMENT ---
# Par defaut un mot-cle matche en PREFIXE : "agri" attrape "agricole",
# "agriculture", "agriculteur". C'est voulu.
# MAIS les acronymes courts ci-dessous doivent etre des mots ISOLES, sinon :
#   "app" attraperait "appel" (present dans "appel d'offres" => toutes les annonces)
#   "ia"  attraperait "special", "materiaux", "financiaire"...
#   "ged" attraperait "budget"... (non, mais "gedimat" oui)
MOTS_EXACTS = {"app", "ia", "web", "data", "ged", "gst", "cloud"}

# --- ZONES PÉPITES ---
SPECIAL_ZONES = ["errachidia", "ouarzazate", "midelt", "tafilalet"]

# --- EXCLUSIONS ---
EXCLUSIONS = [
    "nettoyage", "gardiennage", "construction", "location", "fournitures de bureau", "mobilier", "siège", "chaise",
    "bâtiment", "plomberie", "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
    "espaces verts", "piscine", "vêtement", "habillement", "aménagement", "travaux", "voirie", "topographique",
    "topographie", "billet", "billetterie", "aérien", "ensam", "faculte", "faculté", "université", "école supérieure", "ecole superieure"
]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


# =========================================================
#              RECHERCHE DE MOTS-CLÉS
# =========================================================
@lru_cache(maxsize=4096)
def _pattern(mot):
    """Compile le motif une seule fois par mot (cache).
    \b au debut  : le mot doit commencer une vraie coupure de mot.
    \b a la fin  : uniquement pour les acronymes de MOTS_EXACTS.
    Sans \b final, "agri" attrape bien "agricole" et "agriculture"."""
    m = mot.lower().strip()
    if m in MOTS_EXACTS:
        return re.compile(r"\b" + re.escape(m) + r"\b")
    return re.compile(r"\b" + re.escape(m))


def contient(mot, texte_lower):
    """Remplace `mot in texte` : evite les faux positifs en plein milieu d'un mot."""
    return bool(_pattern(mot).search(texte_lower))


# =========================================================
#                      TELEGRAM
# =========================================================
def send_telegram_to_user(chat_id, message):
    if not TELEGRAM_TOKEN or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": chat_id, "text": message,
            "parse_mode": "Markdown", "disable_web_page_preview": True
        }, timeout=20)
        if r.status_code >= 400:
            log(f"❌ Telegram {chat_id}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        log(f"❌ Erreur envoi Telegram vers {chat_id}: {e}")
        return False


# =========================================================
#                      WHATSAPP
# =========================================================
def wa_clean(text, max_len=280):
    """Les parametres de template WhatsApp interdisent sauts de ligne,
    tabulations et espaces multiples."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t[:max_len] if t else "-"


def send_whatsapp(to, params):
    """Envoie le template WhatsApp. params = liste dans l'ordre {{1}}..{{6}}"""
    if not (WA_TOKEN and WA_PHONE_ID and to):
        return False
    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": str(to),
        "type": "template",
        "template": {
            "name": WA_TEMPLATE,
            "language": {"code": WA_LANG},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": wa_clean(p)} for p in params]
            }]
        }
    }
    try:
        r = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {WA_TOKEN}",
            "Content-Type": "application/json"
        }, timeout=25)
        if r.status_code >= 400:
            code = ""
            try:
                code = r.json().get("error", {}).get("code", "")
            except Exception:
                pass
            hints = {
                132001: "template introuvable (nom/langue incorrects ou pas encore approuve)",
                132000: "nombre de parametres different du template",
                131030: "numero absent de la liste des destinataires de test",
                131047: "hors fenetre 24h (il faut un template, pas du texte libre)",
                190: "token invalide ou expire",
                200: "permission manquante sur le token",
            }
            log(f"❌ WhatsApp {to}: {code} {hints.get(code, r.text[:200])}")
            return False
        return True
    except Exception as e:
        log(f"❌ Erreur envoi WhatsApp vers {to}: {e}")
        return False


def notify(sub, telegram_msg, wa_params):
    """Envoie la meme alerte sur les deux canaux de l'abonne."""
    if sub.get("id"):
        send_telegram_to_user(sub["id"], telegram_msg)
    if sub.get("whatsapp"):
        send_whatsapp(sub["whatsapp"], wa_params)


# =========================================================
#                      PERSISTANCE
# =========================================================
def load_seen():
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            log(f"🧾 Historique charge : {len(data)} offres deja vues")
            return list(data)
    except Exception:
        log("🧾 Aucun historique trouve (premier lancement ou volume absent)")
        return []


def save_seen(seen_list):
    """On garde une LISTE pour conserver l'ordre : la troncature supprime
    alors les plus anciennes, et non des entrees au hasard."""
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(seen_list[-2000:], f)


# =========================================================
#                       SCORING
# =========================================================
def is_pepite(text_lower):
    """Zone prioritaire ou conseil agricole : l'offre doit passer quoi qu'il arrive."""
    return any(contient(z, text_lower) for z in SPECIAL_ZONES) or contient("conseil agri", text_lower)


def scorer(text):
    """Retourne (score, categorie, mots_trouves).
    Cette fonction ne decide PAS du seuil : elle cherche seulement la MEILLEURE
    categorie. Le filtrage est fait dans scan_attempt(), pour que les pepites
    puissent passer outre."""
    text_lower = text.lower()

    for exc in EXCLUSIONS:
        if contient(exc, text_lower):
            return 0, f"Exclu ({exc})", []

    if contient("hébergement", text_lower):
        if not any(contient(x, text_lower) for x in
                   ["web", "site", "cloud", "serveur", "plateforme", "logiciel", "données"]):
            return 0, "Exclu (Hébergement non-IT)", []

    print_words = ["impression", "banderole", "flyer", "imprimerie"]
    training_words = ["formation", "session", "atelier", "renforcement", "sensibilisation"]
    if any(contient(p, text_lower) for p in print_words):
        if not any(contient(t, text_lower) for t in training_words):
            return 0, "Exclu (Impression seule)", []

    # On garde la MEILLEURE categorie, pas la premiere trouvee dans le dict
    best_score, best_cat, best_mots = 0, "Pas de mots-clés", []
    for cat, mots in KEYWORDS.items():
        trouves = [m for m in mots if contient(m, text_lower)]
        if len(trouves) > best_score:
            best_score, best_cat, best_mots = len(trouves), cat, trouves

    return best_score, best_cat, best_mots


def passe_le_seuil(score, category):
    if category.startswith("Exclu") or category == "Pas de mots-clés":
        return False
    if category == "Event & Formation":
        return score >= SEUIL_EVENT
    return score >= SEUIL_DEFAUT


# =========================================================
#                        SCAN
# =========================================================
def scan_attempt():
    seen_list = load_seen()
    seen_ids = set(seen_list)
    pending_alerts = []

    today = datetime.now()
    date_start = today.strftime("%Y-%m-%d")
    date_end = (today + timedelta(days=60)).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--single-process", "--no-zygote"
        ])
        context = browser.new_context(
            viewport={"width": 800, "height": 600},
            user_agent=USER_AGENT,
            locale="fr-FR",
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"}
        )
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2,font}", lambda route: route.abort())

        log(f"🌍 Scan Période : {date_start} -> {date_end}")
        max_pages = 1
        current_page = 1

        while current_page <= max_pages:
            search_url = f"https://www.marchespublics.gov.ma/bdc/entreprise/consultation/?search_consultation_entreprise%5BdateLimiteStart%5D={date_start}&search_consultation_entreprise%5BdateLimiteEnd%5D={date_end}&search_consultation_entreprise%5Bcategorie%5D=3&search_consultation_entreprise%5BpageSize%5D=50&search_consultation_entreprise%5Bpage%5D={current_page}&page={current_page}"

            # 3 tentatives avant d'abandonner : le site est parfois tres lent
            loaded = False
            for attempt in range(1, 4):
                try:
                    page.goto(search_url, timeout=120000, wait_until="domcontentloaded")
                    loaded = True
                    break
                except Exception as e:
                    log(f"⏳ Tentative {attempt}/3 echouee page {current_page} : {str(e)[:80]}")
                    time.sleep(10)

            if not loaded:
                log(f"❌ Page {current_page} inaccessible, scan interrompu.")
                browser.close()
                return False

            if current_page == 1:
                try:
                    res_text = page.locator(".content__resultat").inner_text()
                    num = re.search(r'\d+', res_text)
                    if num:
                        max_pages = math.ceil(int(num.group()) / 50)
                        log(f"🧠 Total : {num.group()} offres ({max_pages} pages)")
                except Exception:
                    pass

            try:
                page.wait_for_selector(".entreprise__card", timeout=15000)
            except Exception:
                log(f"⚠️ Aucune carte trouvee sur la page {current_page}")
                current_page += 1
                continue

            cards = page.locator(".entreprise__card")
            count = cards.count()

            for i in range(count):
                try:
                    card = cards.nth(i)
                    full_text = card.inner_text()

                    offer_id = hashlib.md5(full_text.encode('utf-8')).hexdigest()
                    if offer_id in seen_ids:
                        continue

                    t_lower = full_text.lower()

                    # ⚠️ La pepite est detectee AVANT le filtrage : une offre
                    # a Ouarzazate passe meme si son score est faible.
                    special = is_pepite(t_lower)
                    score, category, mots = scorer(full_text)
                    retenue = passe_le_seuil(score, category)

                    if DEBUG_SCORING:
                        etat = "✅" if (retenue or special) else "❌"
                        log(f"   {etat} score={score} cat={category} pepite={special} mots={mots}")

                    if not (retenue or special):
                        continue

                    # Une pepite sans categorie exploitable est rangee dans "Pépite"
                    if special and not retenue:
                        category = "Pépite"

                    objet = card.locator(".entreprise__middleSubCard a").nth(1).inner_text().replace("Objet :", "").strip()
                    ref = card.locator(".entreprise__middleSubCard a").nth(0).inner_text().strip()

                    date_elements = card.locator(".entreprise__rightSubCard--top .font-bold")
                    date_limite = f"{date_elements.nth(0).inner_text().strip()} à {date_elements.nth(1).inner_text().strip()}"
                    lieu = date_elements.last.inner_text().strip()

                    link_attr = card.locator(".entreprise__middleSubCard a").first.get_attribute("href")
                    link = f"https://www.marchespublics.gov.ma{link_attr}"

                    recipients = [s for s in SUBSCRIBERS
                                  if "ALL" in s["subscriptions"] or category in s["subscriptions"]]
                    if not recipients:
                        log(f"↪️ Ignoree (aucun abonne pour '{category}') : {ref}")
                        continue

                    emoji = "🚜🌾" if contient("agri", t_lower) else "📍🏜️" if special else "🚨"
                    title = "PÉPITE DÉTECTÉE" if special else f"ALERTE {category}"

                    msg = f"{emoji} **{title}**\n━━━━━━━━━━━━\n🎯 Score: {score}\n📅 Limite: `{date_limite}`\n📍 Lieu: `{lieu}`\n━━━━━━━━━━━━\n{ref}\nObjet: {objet}\n\n🔗 [Voir l'offre]({link})"

                    # Parametres WhatsApp {{1}} a {{6}}
                    wa_params = [f"{title} · Score {score}", ref, objet, date_limite, lieu, link]

                    pending_alerts.append({
                        'score': score + (100 if special else 0),
                        'msg': msg,
                        'wa_params': wa_params,
                        'id': offer_id,
                        'recipients': recipients
                    })
                except Exception:
                    continue

            current_page += 1
        browser.close()

    if pending_alerts:
        pending_alerts.sort(key=lambda x: x['score'])
        for item in pending_alerts:
            seen_list.append(item['id'])
            for sub in item['recipients']:
                notify(sub, item['msg'], item['wa_params'])
                time.sleep(0.5)
        save_seen(seen_list)
        log(f"🚀 {len(pending_alerts)} alertes envoyées.")
    else:
        log("Ø Rien de nouveau.")
    return True


def run_loop():
    while True:
        ok = False
        try:
            log("🏁 Démarrage du scan...")
            ok = scan_attempt()
        except Exception as e:
            log(f"⚠️ Erreur: {e}")
        delay = SLEEP_OK if ok else SLEEP_FAIL
        log(f"💤 Sommeil ({delay // 60} min)...")
        time.sleep(delay)


if __name__ == "__main__":
    log("🚀 Bot V5.3 (matching par mot + pepites prioritaires)")

    if not WA_TOKEN:
        log("⚠️ WA_TOKEN absent : WhatsApp desactive, Telegram seul.")

    if not os.path.exists(SEEN_FILE):
        log("⚠️ Pas d'historique : si aucun volume n'est monte sur "
            f"'{os.path.abspath(DATA_PATH)}', les offres seront renvoyees en double.")

    send_telegram_to_user(SUBSCRIBERS[0]["id"], "✅ Bot opérationnel : Telegram + WhatsApp.")

    if WA_TEST and SUBSCRIBERS[0].get("whatsapp"):
        log("🧪 Envoi du WhatsApp de controle...")
        ok = send_whatsapp(SUBSCRIBERS[0]["whatsapp"], [
            "TEST DEMARRAGE", "AO-TEST-001", "Verification du canal WhatsApp",
            "01/01/2027 a 10:00", "Rabat", "https://www.marchespublics.gov.ma"
        ])
        log("🧪 Resultat : " + ("OK ✅" if ok else "ECHEC ❌ (voir erreur ci-dessus)"))

    run_loop()
