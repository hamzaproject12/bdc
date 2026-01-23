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

# Secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app", "digital"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement", "ia"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique", "matériel informatique"],
    "Zakariya": [
        "formation", "sessio n", "atelier", "renforcement de capacité", # Training
        "organisation", "animation", "événement", "sensibilisation",    # Events
        "réception", "pause-café", "restauration", "traiteur",          # Catering (Basé sur l'offre 1)
        "impression", "conception", "banderole", "flyer", "support",    # Print (Basé sur l'offre 7)
        "enquête", "étude", "conseil agricole","conseil","agri"         # Consulting (Basé sur l'offre 8)
        "réunion", 
    ]
}

# EXCLUSIONS = [
#     "restauration", "nettoyage", "gardiennage", "construction", "repas", "traiteur",
#     "fournitures de bureau", "mobilier", "siège", "chaise", "bâtiment", "plomberie",
#     "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
#     "espaces verts", "piscine", "sport", "vêtement", "habillement", "carburant",
#     "véhicule", "transport", "voyage", "billet d'avion", "hôtel", "hébergement des participants",
#     "aménagement", "travaux", "voirie", "restauration", "gardiennage"
# ]

def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def send_telegram(message):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except Exception as e:
        log(f"❌ Erreur Telegram: {e}")

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

    for cat, mots in KEYWORDS.items():
        if any(mot in text_lower for mot in mots):
            return sum(1 for m in mots if m in text_lower), cat
            
    return 0, "Pas de mots-clés"

def run_once():
    log("--- DÉBUT DU CYCLE INTELLIGENT ---")
    seen_ids = load_seen()
    new_ids = set()
    alerts = []

    today = datetime.now()
    future_date = today + timedelta(days=60)
    date_start = today.strftime("%Y-%m-%d")
    date_end = future_date.strftime("%Y-%m-%d")

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            log(f"🌍 Période : {date_start} -> {date_end}")

            max_pages_to_scan = 1 
            current_page = 1

            while current_page <= max_pages_to_scan:
                log(f"📄 [PAGE {current_page}/{max_pages_to_scan}] Chargement...")

                dynamic_url = (
                    f"https://www.marchespublics.gov.ma/bdc/entreprise/consultation/?"
                    f"search_consultation_entreprise%5BdateLimiteStart%5D={date_start}&"
                    f"search_consultation_entreprise%5BdateLimiteEnd%5D={date_end}&"
                    f"search_consultation_entreprise%5Bcategorie%5D=3&"
                    f"search_consultation_entreprise%5BpageSize%5D=50&"
                    f"search_consultation_entreprise%5Bpage%5D={current_page}&"
                    f"page={current_page}"
                )

                try:
                    page.goto(dynamic_url, timeout=60000, wait_until="domcontentloaded")
                    
                    # --- ALGO MAGIQUE : CALCUL DU NOMBRE DE PAGES ---
                    if current_page == 1:
                        try:
                            count_element = page.get_by_text("Nombre de résultats").first
                            if count_element.is_visible():
                                text_content = count_element.inner_text()
                                numbers = re.findall(r'\d+', text_content)
                                if numbers:
                                    total_results = int(numbers[-1])
                                    calculated_pages = math.ceil(total_results / 50)
                                    max_pages_to_scan = calculated_pages
                                    log(f"🧠 INTELLIGENCE : Trouvé {total_results} résultats -> Scan de {max_pages_to_scan} pages.")
                        except Exception as e:
                            log(f"⚠️ Impossible de lire le nombre total : {e}")
                    # -----------------------------------------------

                    try:
                        page.wait_for_selector(".entreprise__card", timeout=10000)
                    except:
                        log(f"⚠️ Page {current_page} vide. Arrêt.")
                        break

                    cards = page.locator(".entreprise__card")
                    count = cards.count()
                    
                    if count == 0: break

                    log(f"🔎 Analyse de {count} offres...")

                    for i in range(count):
                        try:
                            text = cards.nth(i).inner_text()
                            offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                            
                            if offer_id in seen_ids: continue
                            new_ids.add(offer_id)
                            
                            score, details = scorer(text)
                            if score > 0:
                                lines = text.split('\n')
                                raw_objet = next((l for l in lines if "Objet" in l), "Objet inconnu")
                                
                                # --- EXTRACTION INTELLIGENTE DE LA DATE ---
                                # On cherche une date au format JJ/MM/AAAA dans tout le texte
                                # Le texte contient souvent "Date limite ... 26/01/2026"
                                date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                                if "Date limite" in text and date_match:
                                    # On essaie de trouver la date spécifiquement après "Date limite" si possible
                                    specific_match = re.search(r"Date limite.*?(\d{2}/\d{2}/\d{4})", text, re.DOTALL)
                                    deadline = specific_match.group(1) if specific_match else date_match.group(1)
                                else:
                                    deadline = "Date inconnue"
                                # ------------------------------------------

                                log(f"      ✅ PÉPITE (Page {current_page})! Score {score} ({details}) | Date: {deadline}")
                                
                                # Message Telegram avec la date en haut à droite
                                alerts.append(f"🚨 **ALERTE {details}** | ⏳ {deadline}\n{raw_objet}\n[Lien Offre]({dynamic_url})")
                        except: continue
                    
                    time.sleep(2)
                    current_page += 1

                except Exception as e:
                    log(f"❌ Erreur Page {current_page}: {e}")
                    break

            browser.close()

        except Exception as e:
            log(f"❌ Erreur Navigateur: {e}")
            return

    if new_ids:
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        if alerts:
            for msg in alerts: send_telegram(msg)
            log(f"🚀 {len(alerts)} alertes envoyées.")
        else:
            log(f"Ø {len(new_ids)} nouvelles offres vues.")
    else:
        log("Ø Rien de nouveau.")

if __name__ == "__main__":
    log("🚀 Bot Démarré (Version avec Dates Limites)")
    send_telegram("📅 Bot mis à jour : J'affiche maintenant la date limite des devis !")
    
    while True:
        run_once()
        log("💤 Pause de 4 heure...")
        time.sleep(14400)