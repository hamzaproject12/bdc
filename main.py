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

# --- MOTS-CLÉS ---
KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme", "maintenance", "site internet", "app", "digital"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data", "statistique", "traitement", "ia"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur", "réseau", "informatique", "matériel informatique"],
    "Zakariya": [
        "formation", "session", "atelier", "renforcement de capacité", # Training
        "organisation", "animation", "événement", "sensibilisation",    # Events
        "réception", "pause-café", "restauration", "traiteur",          # Catering
        "impression", "conception", "banderole", "flyer", "support",    # Print
        "enquête", "étude", "conseil agricole", "conseil", "agri",      # Consulting
        "réunion"
    ]
}

# --- EXCLUSIONS ---
# J'ai décommenté cette liste car elle est OBLIGATOIRE pour la fonction scorer()
# J'ai retiré les exclusions "traiteur/restauration" pour Zakariya
EXCLUSIONS = [
    "nettoyage", "gardiennage", "construction", 
    "fournitures de bureau", "mobilier", "siège", "chaise", "bâtiment", "plomberie",
    "sanitaire", "toilette", "douche", "peinture", "électricité", "jardinage",
    "espaces verts", "piscine", "vêtement", "habillement", "carburant",
    "véhicule", "transport", "billet d'avion", "hôtel", "hébergement des participants",
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

# --- CŒUR DU SYSTÈME : La fonction qui fait UN essai ---
def scan_attempt():
    """
    Tente de scanner le site UNE fois.
    Si ça plante (Timeout, etc), cette fonction 'lève une erreur' pour que
    la boucle principale sache qu'il faut réessayer.
    """
    seen_ids = load_seen()
    new_ids = set()
    alerts = []

    today = datetime.now()
    future_date = today + timedelta(days=60)
    date_start = today.strftime("%Y-%m-%d")
    date_end = future_date.strftime("%Y-%m-%d")

    with sync_playwright() as p:
        # Configuration Anti-Bot (User Agent)
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        log(f"🌍 Connexion Période : {date_start} -> {date_end}")

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

            # Navigation
            page.goto(dynamic_url, timeout=90000, wait_until="commit")
            
            try:
                page.wait_for_selector("body", timeout=30000)
            except:
                raise Exception("Le site ne répond pas (Body introuvable)")

            # Calcul du nombre de pages (Uniquement page 1)
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

            # Vérification présence des cartes
            try:
                page.wait_for_selector(".entreprise__card", timeout=15000)
            except:
                log(f"⚠️ Page {current_page} semble vide. Arrêt normal.")
                break

            cards = page.locator(".entreprise__card")
            count = cards.count()
            
            if count == 0: break

            log(f"🔎 Analyse de {count} offres en cours...")

            for i in range(count):
                try:
                    text = cards.nth(i).inner_text()

                    # --- AJOUT : Extraction et Affichage du Titre ---
                    lines = text.split('\n')
                    raw_objet = next((l for l in lines if "Objet" in l), "Objet inconnu")
                    # On nettoie le titre pour l'affichage (enlève "Objet :" et coupe à 60 caractères)
                    objet_clean = raw_objet.replace("Objet :", "").replace("\n", "").strip()[:60]
                    
                    log(f"   📄 [{i+1}/{count}] {objet_clean}...")
                    # ------------------------------------------------

                    offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                    
                    if offer_id in seen_ids: continue
                    new_ids.add(offer_id)
                    
                    score, details = scorer(text)
                    if score > 0:
                        # Date extraction
                        date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                        if "Date limite" in text and date_match:
                            specific_match = re.search(r"Date limite.*?(\d{2}/\d{2}/\d{4})", text, re.DOTALL)
                            deadline = specific_match.group(1) if specific_match else date_match.group(1)
                        else:
                            deadline = "Date inconnue"

                        log(f"      ✅ PÉPITE! Score {score} ({details})")
                        alerts.append(f"🚨 **ALERTE {details}** | ⏳ {deadline}\n{raw_objet}\n[Lien Offre]({dynamic_url})")
                except: continue
            
            time.sleep(2)
            current_page += 1

        browser.close()

    # Si on arrive ici, c'est que le scan s'est bien passé
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
    
    return True # Succès

# --- GESTION DES RELANCES ---
def run_with_retries():
    MAX_RETRIES = 3
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"🏁 Démarrage Scan (Tentative {attempt}/{MAX_RETRIES})...")
            
            # On lance le scan. Si ça plante, ça part dans "except Exception"
            success = scan_attempt()
            
            if success:
                return # Tout s'est bien passé, on sort de la boucle et on va dormir
                
        except Exception as e:
            log(f"⚠️ ERREUR TENTATIVE {attempt} : {e}")
            
            if attempt < MAX_RETRIES:
                wait_time = 60 # On attend 60 secondes avant de réessayer
                log(f"⏳ Attente de {wait_time}s avant nouvelle tentative...")
                time.sleep(wait_time)
            else:
                # C'était la dernière tentative
                log("❌ ECHEC TOTAL après 3 tentatives.")
                send_telegram(f"❌ **ALERTE TECHNIQUE BOT**\nLe scan a échoué 3 fois de suite.\nErreur : {e}\nJe passe en mode pause 4h.")

if __name__ == "__main__":
    log("🚀 Bot Démarré (Mode Logs Détaillés + Robustesse)")
    send_telegram("👀 Bot mis à jour : Je t'affiche tout ce que je lis dans les logs !")
    
    while True:
        run_with_retries()
        log("💤 Pause de 4 heures...")
        time.sleep(14400) # 4 heures