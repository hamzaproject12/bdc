import time
import json
import requests
import hashlib
import os
import math 
import re    
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
DATA_PATH = "data"
SEEN_FILE = os.path.join(DATA_PATH, "seen_offers.json")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- 👥 CONFIGURATION DES ABONNÉS ---
SUBSCRIBERS = [
    {"name": "Moi", "id": "1952904877", "subscriptions": ["ALL"]},
    # {"name": "Yassine", "id": "7879373928", "subscriptions": ["Event & Formation"]},
    # {"name": "Zakariya", "id": "8260779046", "subscriptions": ["Event & Formation"]}
]

# --- MOTS-CLÉS ---
KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app", "digital"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement", "ia"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique", "matériel informatique"],
    "Event & Formation": ["formation", "atelier", "renforcement de capacité", "organisation", "animation", "sensibilisation", "impression", "conception", "enquête", "étude", "conseil agricole", "conseil", "agri"]
}

# --- EXCLUSIONS ---
EXCLUSIONS = [
    "nettoyage", "gardiennage", "construction", "location", "fournitures de bureau", "mobilier", "siège", "chaise", 
    "bâtiment", "plomberie", "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
    "espaces verts", "piscine", "vêtement", "habillement", "aménagement", "travaux", "voirie", "topographique", 
    "topographie", "billet", "billetterie", "aérien", "ensam", "faculte", "faculté", "université", "école supérieure", "ecole superieure"
]

def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def send_telegram_to_user(chat_id, message):
    if not TELEGRAM_TOKEN or not chat_id: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True})
    except Exception as e:
        log(f"❌ Erreur envoi vers {chat_id}: {e}")

def load_seen():
    if not os.path.exists(DATA_PATH): os.makedirs(DATA_PATH, exist_ok=True)
    try:
        with open(SEEN_FILE, "r") as f: return set(json.load(f))
    except: return set()

def save_seen(seen_set):
    # On ne garde que les 2000 derniers IDs pour économiser la RAM sur le long terme
    list_ids = list(seen_set)[-2000:]
    with open(SEEN_FILE, "w") as f: json.dump(list_ids, f)

def scorer(text):
    text_lower = text.lower()
    for exc in EXCLUSIONS:
        if exc in text_lower: return 0, f"Exclu ({exc})"
    
    if "hébergement" in text_lower:
        if not any(x in text_lower for x in ["web", "site", "cloud", "serveur", "plateforme", "logiciel", "données"]):
            return 0, "Exclu (Hébergement non-IT)"

    print_words = ["impression", "banderole", "flyer", "imprimerie"]
    training_words = ["formation", "session", "atelier", "renforcement", "sensibilisation"]
    if any(p in text_lower for p in print_words):
        if not any(t in text_lower for t in training_words):
            return 0, "Exclu (Impression seule)"

    for cat, mots in KEYWORDS.items():
        if any(mot in text_lower for mot in mots):
            return sum(1 for m in mots if m in text_lower), cat
    return 0, "Pas de mots-clés"

def scan_attempt():
    seen_ids = load_seen()
    new_ids = set()
    pending_alerts = [] 

    today = datetime.now()
    date_start = today.strftime("%Y-%m-%d")
    date_end = (today + timedelta(days=60)).strftime("%Y-%m-%d")

    with sync_playwright() as p:
        # --- OPTIMISATION LANCEMENT (RAM) ---
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", 
            "--disable-gpu", "--single-process", "--no-zygote"
        ])
        # Viewport réduit pour consommer moins de mémoire
        context = browser.new_context(viewport={"width": 1024, "height": 768})
        page = context.new_page()

        # --- BLOCAGE RESSOURCES INUTILES (Gain RAM majeur) ---
        page.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2,font}", lambda route: route.abort())

        log(f"🌍 Scan Période : {date_start} -> {date_end}")
        max_pages = 1 
        current_page = 1

        while current_page <= max_pages:
            search_url = f"https://www.marchespublics.gov.ma/bdc/entreprise/consultation/?search_consultation_entreprise%5BdateLimiteStart%5D={date_start}&search_consultation_entreprise%5BdateLimiteEnd%5D={date_end}&search_consultation_entreprise%5Bcategorie%5D=3&search_consultation_entreprise%5BpageSize%5D=50&search_consultation_entreprise%5Bpage%5D={current_page}&page={current_page}"
            
            page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
            
            if current_page == 1:
                try:
                    res_text = page.locator(".content__resultat").inner_text()
                    num = re.search(r'\d+', res_text)
                    if num:
                        max_pages = math.ceil(int(num.group()) / 50)
                        log(f"🧠 Total : {num.group()} offres ({max_pages} pages)")
                except: pass

            page.wait_for_selector(".entreprise__card", timeout=10000)
            cards = page.locator(".entreprise__card")
            count = cards.count()

            for i in range(count):
                try:
                    card = cards.nth(i)
                    full_text = card.inner_text()
                    
                    # Identifiant unique basé sur le texte de l'offre
                    offer_id = hashlib.md5(full_text.encode('utf-8')).hexdigest()
                    if offer_id in seen_ids: continue

                    score, category = scorer(full_text)
                    if score > 0:
                        # Extraction propre via sélecteurs basés sur votre HTML
                        objet = card.locator(".entreprise__middleSubCard a").nth(1).inner_text().replace("Objet :", "").strip()
                        ref = card.locator(".entreprise__middleSubCard a").nth(0).inner_text().strip()
                        date_limite = card.locator(".entreprise__rightSubCard--top .font-bold").first.inner_text().strip()
                        lieu = card.locator(".entreprise__rightSubCard--top .font-bold").last.inner_text().strip()
                        
                        link_attr = card.locator(".entreprise__middleSubCard a").first.get_attribute("href")
                        link = f"https://www.marchespublics.gov.ma{link_attr}"

                        recipients = [s["id"] for s in SUBSCRIBERS if "ALL" in s["subscriptions"] or category in s["subscriptions"]]
                        if not recipients: continue

                        # Tags spéciaux
                        t_lower = full_text.lower()
                        is_special = any(c in t_lower for c in ["errachidia", "ouarzazate", "midelt", "tafilalet"]) or "conseil agri" in t_lower
                        
                        emoji = "🚜🌾" if "agri" in t_lower else "📍🏜️" if is_special else "🚨"
                        title = "PÉPITE DÉTECTÉE" if is_special else f"ALERTE {category}"

                        msg = f"{emoji} **{title}**\n━━━━━━━━━━━━\n🎯 Score: {score}\n📅 Limite: `{date_limite}`\n📍 Lieu: `{lieu}`\n━━━━━━━━━━━━\n{ref}\nObjet: {objet[:150]}...\n\n🔗 [Voir l'offre]({link})"
                        
                        pending_alerts.append({'score': score + (100 if is_special else 0), 'msg': msg, 'id': offer_id, 'recipients': recipients})
                except Exception as e:
                    continue
            
            current_page += 1
        browser.close()

    if pending_alerts:
        pending_alerts.sort(key=lambda x: x['score'], reverse=True)
        for item in pending_alerts:
            new_ids.add(item['id'])
            for uid in item['recipients']: send_telegram_to_user(uid, item['msg'])
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        log(f"🚀 {len(pending_alerts)} alertes envoyées.")
    else:
        log("Ø Rien de nouveau.")
    return True

def run_loop():
    while True:
        try:
            log("🏁 Démarrage du scan...")
            scan_attempt()
        except Exception as e:
            log(f"⚠️ Erreur: {e}")
        log("💤 Sommeil (4h)...")
        time.sleep(14400)

if __name__ == "__main__":
    log("🚀 Bot V4.1 (Optimisé RAM & HTML)")
    send_telegram_to_user(SUBSCRIBERS[0]["id"], "✅ Bot redémarré en mode basse consommation (RAM).")
    run_loop()