import os
import json
import smtplib
import datetime
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF

STATE_FILE = "state.json"

NAMES_TO_SEARCH = [
    "lucas mangueira",
    "luis mauro albuquerque araújo",
]

RECIPIENT_EMAIL = "lucasmangueira@luthy.com.br"

SENDER_EMAIL = os.environ.get("SMTP_USER")
SENDER_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def already_found_today(state):
    today = datetime.date.today().isoformat()
    return state.get("last_found_date") == today


def mark_found_today(state):
    today = datetime.date.today().isoformat()
    state["last_found_date"] = today
    save_state(state)


def get_last_published_date():
    """Verifica se o diário de hoje já foi publicado. Retorna True se sim."""
    url = "http://pesquisa.doe.seplag.ce.gov.br/doepesquisa/sead.do?page=ultimasEdicoes&cmd=11&action=Ultimas"
    
    try:
        resp = requests.get(url, timeout=45)  # timeout maior
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        texto = soup.get_text()
        match = re.search(r"Último Diário publicado\s*\(\s*(\d{2}/\d{2}/\d{4})\s*\)", texto)
        if match:
            data_str = match.group(1)
            last_date = datetime.datetime.strptime(data_str, "%d/%m/%Y").date()
            today = datetime.date.today()
            return last_date == today
        
        print("Não encontrou data na página de últimas edições")
        return False
        
    except Exception as e:
        print(f"Erro ao verificar últimas edições: {e}. Tentando PDF mesmo assim...")
        return True  # fallback: tenta baixar PDF mesmo se não conseguir acessar a página


def build_pdf_url_for_date(date_obj):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"http://imagens.seplag.ce.gov.br/PDF/{date_str}/do{date_str}p01.pdf"
    return url


def download_pdf_bytes(url):
    resp = requests.get(url, timeout=90)  # timeout bem maior para PDF
    if resp.status_code != 200:
        raise RuntimeError(f"Erro ao baixar PDF: status {resp.status_code}")
    return resp.content


def extract_text_from_pdf_bytes(pdf_bytes: bytes):
    pdf_stream = BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    full_text = []
    for page in doc:
        text = page.get_text()
        full_text.append(text)
    doc.close()
    return "\n".join(full_text)


def search_names_in_text(text, names):
    text_lower = text.lower()
    found = []
    for name in names:
        if name.lower() in text_lower:
            found.append(name)
    return found


def send_email(subject: str, body: str):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Configuração de SMTP ausente. Defina SMTP_USER e SMTP_PASSWORD.")
        return

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print("E-mail enviado para", RECIPIENT_EMAIL)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")


def main():
    print("=== Bot DOE/CE iniciado ===")
    state = load_state()
    if already_found_today(state):
        print("Já encontrou correspondência hoje. Nada a fazer.")
        return

    # Verificar se o diário de hoje já foi publicado
    diario_hoje_publicado = get_last_published_date()
    
    if not diario_hoje_publicado:
        print("Diário de hoje ainda não foi publicado. Aguardando...")
        return

    print("Diário de hoje publicado. Baixando PDF...")
    
    today = datetime.date.today()
    try:
        url = build_pdf_url_for_date(today)
        print(f"Baixando: {url}")
        pdf_bytes = download_pdf_bytes(url)
    except Exception as e:
        print("Não foi possível baixar o PDF de hoje:", e)
        return

    try:
        text = extract_text_from_pdf_bytes(pdf_bytes)
    except Exception as e:
        print("Erro ao extrair texto do PDF:", e)
        return

    found_names = search_names_in_text(text, NAMES_TO_SEARCH)

    if found_names:
        today_br = today.strftime("%d/%m/%Y")
        subject = f"[BOT DOE/CE] Nome(s) encontrado(s) no Diário Oficial em {today_br}"
        lines = [
            f"Foi encontrada correspondência no Diário Oficial do Estado do Ceará em {today_br}.",
            "",
            "Nomes encontrados:",
        ]
        for name in found_names:
            lines.append(f"- {name}")
        lines.append("")
        lines.append(f"Link do PDF: {url}")
        body = "\n".join(lines)

        send_email(subject, body)
        mark_found_today(state)
        print("ACHADO! E-mail enviado e marcado como encontrado hoje.")
    else:
        print("Nenhum dos nomes foi encontrado hoje.")


if __name__ == "__main__":
    main()
