import time
import json
import requests
import hashlib
import os
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
DATA_PATH = "data"
SEEN_FILE = os.path.join(DATA_PATH, "seen_offers.json")

# Secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique"]
}

# Liste d'exclusion
EXCLUSIONS = [
    "restauration", "nettoyage", "gardiennage", "construction", "repas", "traiteur",
    "fournitures de bureau", "mobilier", "siège", "chaise", "bâtiment", "plomberie",
    "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
    "espaces verts", "piscine", "sport", "vêtement", "habillement", "carburant",
    "véhicule", "transport", "voyage", "billet d'avion", "hôtel", "hébergement des participants",
    "aménagement", "travaux", "voirie"
]

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
    log("--- DÉBUT DU CYCLE ---")
    seen_ids = load_seen()
    new_ids = set()
    alerts = []

    # Calcul des dates pour l'URL dynamique
    today = datetime.now()
    future_date = today + timedelta(days=60)
    date_start = today.strftime("%Y-%m-%d")
    date_end = future_date.strftime("%Y-%m-%d")

    # URL : Dates Dynamiques + Catégorie Services (3) + 50 résultats
    dynamic_url = (
        f"https://www.marchespublics.gov.ma/bdc/entreprise/consultation/?"
        f"search_consultation_entreprise%5BdateLimiteStart%5D={date_start}&"
        f"search_consultation_entreprise%5BdateLimiteEnd%5D={date_end}&"
        f"search_consultation_entreprise%5Bcategorie%5D=3&"
        f"search_consultation_entreprise%5BpageSize%5D=50"
    )

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            log(f"🌍 Scan de la période : {date_start} au {date_end}")
            page.goto(dynamic_url, timeout=60000, wait_until="domcontentloaded")
            
            try:
                page.wait_for_selector(".entreprise__card", timeout=15000)
            except:
                log("⚠️ Aucune carte affichée (page vide ou lente)")

            cards = page.locator(".entreprise__card")
            count = cards.count()
            log(f"🔎 {count} offres trouvées. Analyse en cours :")

            for i in range(count):
                try:
                    text = cards.nth(i).inner_text()
                    
                    # --- LOGS DES TITRES ---
                    lines = text.split('\n')
                    raw_objet = next((l for l in lines if "Objet" in l), "Objet inconnu")
                    objet_clean = raw_objet.replace("Objet :", "").replace("\n", "").strip()[:60]
                    
                    log(f"   📄 [{i+1}/{count}] {objet_clean}...")
                    # -----------------------

                    offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                    
                    if offer_id in seen_ids: continue
                    new_ids.add(offer_id)
                    
                    score, details = scorer(text)
                    
                    if score > 0:
                        log(f"      ✅ PÉPITE ! Score {score} ({details})")
                        alerts.append(f"🚨 **ALERTE {details}** (Score {score})\n{raw_objet}\n[Voir l'offre]({dynamic_url})")
                    else:
                        pass # log(f"      ❌ Rejeté : {details}")
                    
                except Exception as e: continue

            browser.close()

        except Exception as e:
            log(f"❌ Erreur : {e}")
            return

    if new_ids:
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        if alerts:
            for msg in alerts: send_telegram(msg)
            log(f"🚀 {len(alerts)} alertes envoyées.")
        else:
            log(f"Ø {len(new_ids)} nouvelles offres (aucune intéressante).")
    else:
        log("Ø Rien de nouveau.")

if __name__ == "__main__":
    log("🚀 Bot Démarré (Version Logs Détaillés + Dates)")
    send_telegram("🚀 Mise à jour active : Logs détaillés et Dates Dynamiques !")
    
    while True:
        run_once()
        log("💤 Pause de 1 heure...")
        time.sleep(120)