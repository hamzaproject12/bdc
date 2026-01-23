import time
import json
import requests
import hashlib
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
URL_CONSULTATION = "https://www.marchespublics.gov.ma/bdc/entreprise/consultation/"
DATA_PATH = "data"  # Chemin relatif (plus sûr sur Railway sans volume)
SEEN_FILE = os.path.join(DATA_PATH, "seen_offers.json")

# Secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS = {
    "Dév & Web": ["développement", "application", "web", "portail", "logiciel", "plateforme","maintenance"],
    "Data": ["données", "data", "numérisation", "archivage", "ged", "big data"],
    "Infra": ["hébergement", "cloud", "maintenance", "sécurité", "serveur"]
}

# Liste d'exclusion améliorée (Anti-Toilettes)
EXCLUSIONS = [
    "restauration", "nettoyage", "gardiennage"
]

def log(msg):
    # Ajoute l'heure pour bien suivre les logs
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def send_telegram(message):
    if not TELEGRAM_TOKEN:
        log("⚠️ Pas de Token Telegram configuré")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        log(f"📤 Tentative envoi Telegram: {message[:30]}...")
        response = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"})
        if response.status_code != 200:
            log(f"❌ Erreur Telegram: {response.text}")
        else:
            log("✅ Message Telegram envoyé avec succès")
    except Exception as e:
        log(f"❌ Exception Telegram: {e}")

def load_seen():
    if not os.path.exists(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
        log(f"📁 Dossier {DATA_PATH} créé.")
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            log(f"📂 Mémoire chargée : {len(data)} offres déjà vues.")
            return set(data)
    except:
        log("📂 Aucune mémoire trouvée (premier lancement ou fichier perdu).")
        return set()

def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w") as f: json.dump(list(seen_set), f)
        log("💾 Mémoire sauvegardée.")
    except Exception as e:
        log(f"❌ Erreur sauvegarde mémoire: {e}")

def scorer(text):
    text_lower = text.lower()
    
    # Debug exclusions
    for exc in EXCLUSIONS:
        if exc in text_lower:
            return 0, f"Exclu ({exc})"
            
    # Cas spécial Hébergement
    if "hébergement" in text_lower:
        if not any(x in text_lower for x in ["web", "site", "cloud", "serveur", "plateforme", "logiciel", "données"]):
            return 0, "Exclu (Hébergement non-IT)"

    for cat, mots in KEYWORDS.items():
        if any(mot in text_lower for mot in mots):
            matched = [m for m in mots if m in text_lower]
            return len(matched), cat
            
    return 0, "Pas de mots-clés"

def run_once():
    log("--- DÉBUT DU CYCLE ---")
    seen_ids = load_seen()
    new_ids = set()
    alerts = []

    # Message de vie pour le test (A supprimer plus tard)
    send_telegram(f"🔍 Scan lancé... ({len(seen_ids)} en mémoire)")

    with sync_playwright() as p:
        log("🚀 Lancement du navigateur...")
        try:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            log(f"🌍 Connexion à {URL_CONSULTATION}...")
            page.goto(URL_CONSULTATION, timeout=90000, wait_until="domcontentloaded")
            
            # Filtre Services
            try:
                if page.is_visible("button.content-icon__settings"):
                    log("🖱️ Clic sur filtre avancé...")
                    page.click("button.content-icon__settings")
                    time.sleep(1)
                
                log("👇 Sélection catégorie 'Services'...")
                page.select_option("#search_consultation_categorie", "3")
                
                log("🖱️ Clic sur Rechercher...")
                page.click("button.sendform", force=True)
                page.wait_for_load_state("networkidle")
                time.sleep(5) # Attente chargement résultats
            except Exception as e:
                log(f"⚠️ Problème interface (filtres): {e}")

            cards = page.locator(".entreprise__card")
            count = cards.count()
            log(f"🔎 {count} offres trouvées sur la page.")

            # Analyse détaillée
            for i in range(count):
                try:
                    text = cards.nth(i).inner_text()
                    lines = text.split('\n')
                    objet = next((l for l in lines if "Objet" in l), "Objet inconnu")[:50] # On prend juste les 50 premiers caractères
                    
                    offer_id = hashlib.md5(text.encode('utf-8')).hexdigest()
                    
                    # LOGIQUE DE DÉCISION TRACÉE
                    if offer_id in seen_ids:
                        # log(f"   [DÉJÀ VU] {objet}...") # Décommenter si tu veux voir même les anciens
                        continue
                    
                    new_ids.add(offer_id)
                    
                    score, details = scorer(text)
                    
                    if score > 0:
                        log(f"✅ [TROUVÉ !] Score {score} | Cat: {details} | Objet: {objet}...")
                        full_obj = next((l for l in lines if "Objet" in l), "Objet inconnu")
                        alerts.append(f"🚨 **{details}** (Score {score})\n{full_obj}\n[Lien]({URL_CONSULTATION})")
                    else:
                        pass
                        # log(f"   [REJETÉ] {details} | {objet}...") # Décommenter pour voir les rejets

                except Exception as e: 
                    log(f"❌ Erreur lecture carte {i}: {e}")

            browser.close()
            log("🛑 Navigateur fermé.")

        except Exception as e:
            log(f"❌ CRASH NAVIGATEUR: {e}")
            send_telegram(f"🔥 Crash Bot: {e}")
            return # On sort

    if new_ids:
        log(f"📝 {len(new_ids)} nouvelles offres ajoutées à la mémoire.")
        seen_ids.update(new_ids)
        save_seen(seen_ids)
        
        if alerts:
            log(f"🚀 Envoi de {len(alerts)} alertes Telegram...")
            for msg in alerts: send_telegram(msg)
        else:
            log("Ø Aucune alerte pertinente parmi les nouvelles offres.")
    else:
        log("Ø Rien de nouveau (tout était déjà vu).")

if __name__ == "__main__":
    log("🏁 PRÊT AU DÉCOLLAGE SUR RAILWAY")
    send_telegram("🏁 Bot initialisé avec Logs Bavards")
    
    while True:
        run_once()
        log("💤 Dodo 2 minutes...")
        time.sleep(120)