import time
import json
import requests
import hashlib
import os
from playwright.sync_api import sync_playwright

# --- CONFIGURATION RAILWAY ---
URL_CONSULTATION = "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/"
# On utilise un chemin absolu pour le volume persistant (voir étape 3)
DATA_PATH = "/app/data" 
SEEN_FILE = os.path.join(DATA_PATH, "seen_offers.json")

# Récupération des secrets depuis les Variables d'Environnement Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme"],
    "Data": ["données", "data", "numérisation", "archivage", "ged"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité"]
}
EXCLUSIONS = ["restauration", "nettoyage", "gardiennage", "construction"]

def send_telegram(message):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
    except: pass

def load_seen():
    # Création du dossier data s'il n'existe pas
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
        if exc in text_lower: return 0, None
    for cat, mots in KEYWORDS.items():
        if any(mot in text_lower for mot in mots):
            return sum(1 for m in mots if m in text_lower), cat
    return 0, None

def run_once():
    seen_ids = load_seen()
    new_ids = set()
    alerts = []

    print("🔄 Démarrage du scan...")
    with sync_playwright() as p:
        # Important : Arguments pour tourner sur Docker sans crasher
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        try:
            page.goto(URL_CONSULTATION, timeout=90000, wait_until="domcontentloaded")
            
            # Filtre Services
            if page.is_visible("button.content-icon__settings"):
                page.click("button.content-icon__settings")
                time.sleep(1)
            
            # Note: Parfois l'ID change sur la version mobile/desktop, on sécurise
            try: page.select_option("#search_consultation_categorie", "3")
            except: pass 
            
            page.click("button.sendform", force=True)
            page.wait_for_load_state("networkidle")
            time.sleep(5) # Sécurité chargement

            cards = page.locator(".entreprise__card")
            count = cards.count()
            print(f"🔎 {count} offres trouvées.")

            for i in range(count):
                text = cards.nth(i).inner_text()
                offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                
                if offer_id in seen_ids: continue
                new_ids.add(offer_id)
                
                score, cat = scorer(text)
                if score > 0:
                    lines = text.split('\n')
                    objet = next((l for l in lines if "Objet" in l), "Objet inconnu")
                    alerts.append(f"🚨 **ALERTE {cat}** (Score {score})\n{objet}\n[Lien]({URL_CONSULTATION})")

        except Exception as e:
            print(f"Erreur: {e}")
        
        browser.close()

    if new_ids:
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        for msg in alerts: send_telegram(msg)
        print(f"✅ {len(new_ids)} nouvelles offres traitées.")
    else:
        print("Ø Rien de nouveau.")

# --- BOUCLE INFINIE POUR LE SERVEUR ---
if __name__ == "__main__":
    print("🚀 Bot Démarré sur Railway")
    while True:
        run_once()
        print("💤 Pause de 4 heures...")
        # 4 heures = 14400 secondes. Le bot vérifie 6 fois par jour.
        time.sleep(14400)