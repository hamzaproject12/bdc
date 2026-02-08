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
    {
        "name": "Moi",
        "id": "1952904877",
        "subscriptions": ["ALL"] 
    },
    # {
    #     "name": "Yassine",
    #     "id": "7879373928",
    #     "subscriptions": ["Event & Formation"] 
    # },
    # {
    #     "name": "Zakariya",
    #     "id": "8260779046", 
    #     "subscriptions": ["Event & Formation"] 
    # }
]

# --- MOTS-CLÉS ---
KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app", "digital"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement", "ia"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique", "matériel informatique"],
    
    "Event & Formation": [
        "formation", "atelier", "renforcement de capacité", 
        "organisation", "animation", "sensibilisation",           
        "impression", "conception",    
        "enquête", "étude", "conseil agricole", "conseil", "agri"
    ]
}

# --- EXCLUSIONS ---
EXCLUSIONS = [
    "nettoyage", "gardiennage", "construction", "location"
    "fournitures de bureau", "mobilier", "siège", "chaise", "bâtiment", "plomberie",
    "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
    "espaces verts", "piscine", "vêtement", "habillement",
    "aménagement", "travaux", "voirie", 
    
    # NOUVELLES EXCLUSIONS
    "topographique", "topographie", 
    "billet", "billetterie", "aérien",
    "ensam", "faculte", "faculté", "université", "école supérieure","ecole superieure"
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
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
    try:
        with open(SEEN_FILE, "r") as f: return set(json.load(f))
    except: return set()

def save_seen(seen_set):
    with open(SEEN_FILE, "w") as f: json.dump(list(seen_set), f)

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
        has_training = any(t in text_lower for t in training_words)
        if not has_training:
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
    future_date = today + timedelta(days=60)
    date_start = today.strftime("%Y-%m-%d")
    date_end = future_date.strftime("%Y-%m-%d")

    with sync_playwright() as p:
        # OPTIMISATION RAM 1 : Flags Chromium pour environnements limités
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", 
            "--disable-setuid-sandbox", 
            "--disable-dev-shm-usage", 
            "--disable-gpu",
            "--single-process"
        ])
        
        # OPTIMISATION RAM 2 : Réduction du Viewport (charge moins de pixels)
        context = browser.new_context(
            viewport={"width": 800, "height": 600},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # OPTIMISATION RAM 3 : Bloquer images et CSS (indispensable sur Railway)
        page.route("**/*.{png,jpg,jpeg,svg,css,woff,woff2}", lambda route: route.abort())

        log(f"🌍 Connexion Période : {date_start} -> {date_end}")

        max_pages_to_scan = 1 
        current_page = 1

        while current_page <= max_pages_to_scan:
            log(f"📄 [PAGE {current_page}/{max_pages_to_scan}] Chargement...")

            search_url = (
                f"https://www.marchespublics.gov.ma/bdc/entreprise/consultation/?"
                f"search_consultation_entreprise%5BdateLimiteStart%5D={date_start}&"
                f"search_consultation_entreprise%5BdateLimiteEnd%5D={date_end}&"
                f"search_consultation_entreprise%5Bcategorie%5D=3&"
                f"search_consultation_entreprise%5BpageSize%5D=50&"
                f"search_consultation_entreprise%5Bpage%5D={current_page}&"
                f"page={current_page}"
            )

            page.goto(search_url, timeout=90000, wait_until="commit")
            
            try:
                page.wait_for_selector("body", timeout=30000)
            except:
                raise Exception("Le site ne répond pas")

            if current_page == 1:
                try:
                    time.sleep(2) 
                    count_element = page.get_by_text("Nombre de résultats").first
                    if count_element.is_visible():
                        text_content = count_element.inner_text()
                        numbers = re.findall(r'\d+', text_content)
                        if numbers:
                            total_results = int(numbers[-1])
                            max_pages_to_scan = math.ceil(total_results / 50)
                            log(f"🧠 INTELLIGENCE : {total_results} offres -> {max_pages_to_scan} pages.")
                except: pass

            try:
                page.wait_for_selector(".entreprise__card", timeout=15000)
            except:
                log(f"⚠️ Page {current_page} vide.")
                break

            cards = page.locator(".entreprise__card")
            count = cards.count()
            if count == 0: break

            log(f"🔎 Analyse de {count} offres...")

            for i in range(count):
                try:
                    card_element = cards.nth(i)
                    text = card_element.inner_text()

                    lines = text.split('\n')
                    raw_objet = next((l for l in lines if "Objet" in l), "Objet inconnu")
                    objet_clean = raw_objet.replace("Objet :", "").replace("\n", "").strip()[:60]
                    log(f"   📄 [{i+1}/{count}] {objet_clean}...")

                    offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                    if offer_id in seen_ids: continue
                    
                    score, matched_category = scorer(text) 
                    
                    if score > 0:
                        recipients = []
                        for sub in SUBSCRIBERS:
                            if "ALL" in sub["subscriptions"] or matched_category in sub["subscriptions"]:
                                recipients.append(sub["id"])
                        
                        if not recipients: continue

                        deadline_str = "Date inconnue"
                        if "Date limite" in text:
                            full_date_match = re.search(r"(\d{2}/\d{2}/\d{4})(?:\s+|\n+)(\d{2}:\d{2})", text)
                            if full_date_match:
                                deadline_str = f"{full_date_match.group(1)} à {full_date_match.group(2)}"
                            else:
                                simple_date = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                                if simple_date: deadline_str = simple_date.group(1)

                        final_link = search_url 
                        try:
                            href = card_element.locator("a").first.get_attribute("href")
                            if href:
                                final_link = href if href.startswith("http") else f"https://www.marchespublics.gov.ma{href}"
                        except: pass

                        text_lower = text.lower()
                        is_agri = "conseil agri" in text_lower or "conseil agricole" in text_lower
                        
                        target_cities = ["errachidia", "ouarzazate", "tafilalel", "tafilalet", "midelt"]
                        found_city = next((city for city in target_cities if city in text_lower), None)

                        msg_text = ""
                        is_special = False

                        if is_agri:
                            is_special = True
                            log(f"      🚜 PÉPITE AGRI DÉTECTÉE !")
                            msg_text = (
                                f"🚨🚜🌾 **CONSEIL AGRICOLE** 🌾🚜🚨\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏛️ *Sujet :* {matched_category} (Score {score})\n"
                                f"📅 *Limite :* `{deadline_str}`\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{raw_objet}\n\n"
                                f"🔗 [VOIR L'OFFRE MAINTENANT]({final_link})"
                            )

                        elif found_city:
                            is_special = True
                            city_upper = found_city.upper()
                            log(f"      📍 PÉPITE RÉGION DÉTECTÉE ({city_upper}) !")
                            msg_text = (
                                f"🚨📍🏜️ **ALERTE ZONE : {city_upper}** 🏜️📍🚨\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏛️ *Sujet :* {matched_category} (Score {score})\n"
                                f"📅 *Limite :* `{deadline_str}`\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{raw_objet}\n\n"
                                f"🔗 [VOIR L'OFFRE MAINTENANT]({final_link})"
                            )

                        else:
                            log(f"      ✅ Pépite standard ({matched_category})")
                            msg_text = (
                                f"🚨 **ALERTE {matched_category}**\n"
                                f"⏳ *{deadline_str}* | 🎯 Score: *{score}*\n\n"
                                f"{raw_objet}\n\n"
                                f"🔗 [Voir l'offre]({final_link})"
                            )
                        
                        pending_alerts.append({
                            'score': score + (100 if is_special else 0), 
                            'msg': msg_text,
                            'id': offer_id,
                            'recipients': recipients 
                        })
                        
                except Exception as e: 
                    log(f"❌ Erreur lecture carte: {e}")
                    continue
            
            time.sleep(2)
            current_page += 1

        browser.close()

    if pending_alerts:
        pending_alerts.sort(key=lambda x: x['score'], reverse=True)
        count_sent = 0
        for item in pending_alerts:
            new_ids.add(item['id'])
            for user_id in item['recipients']:
                send_telegram_to_user(user_id, item['msg'])
            count_sent += 1
        
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        log(f"🚀 {count_sent} alertes traitées.")
    else:
        log("Ø Rien de nouveau.")
    
    return True 

def run_with_retries():
    MAX_RETRIES = 3
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"🏁 Démarrage Scan (Tentative {attempt}/{MAX_RETRIES})...")
            success = scan_attempt()
            if success: return 
        except Exception as e:
            log(f"⚠️ ERREUR TENTATIVE {attempt} : {e}")
            if attempt < MAX_RETRIES:
                time.sleep(60)
            else:
                log("❌ ECHEC TOTAL.")
                send_telegram_to_user(SUBSCRIBERS[0]["id"], f"❌ Crash Bot: {e}")

if __name__ == "__main__":
    log("🚀 Bot Démarré (V4: Filtrage Avancé ENSAM/Impression)")
    send_telegram_to_user(SUBSCRIBERS[0]["id"], "🧹 Bot mis à jour : Je filtre les tickets, l'ENSAM et l'impression seule !")
    
    while True:
        run_with_retries()
        log("💤 Pause de 4 heures...")
        time.sleep(14400)