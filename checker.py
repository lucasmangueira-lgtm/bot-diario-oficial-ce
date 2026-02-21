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
    """Verifica se o diário de hoje já foi publicado."""
    url = "http://pesquisa.doe.seplag.ce.gov.br/doepesquisa/sead.do?page=ultimasEdicoes&cmd=11&action=Ultimas"
    
    try:
        resp = requests.get(url, timeout=45)
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
        print(f"Erro ao verificar últimas edições: {e}. Usando fallback...")
        return True  # fallback


def build_pdf_url_for_date(date_obj):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"http://imagens.seplag.ce.gov.br/PDF/{date_str}/do{date_str}p01.pdf"
    return url


def download_pdf_bytes(url):
    resp = requests.get(url, timeout=90, allow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"Erro ao baixar PDF: status {resp.status_code}")
    print(f"PDF baixado: {len(resp.content)} bytes (final URL: {resp.url})")
    return resp.content


def extract_pdf_date_and_text(pdf_bytes: bytes):
    """Extrai a data do cabeçalho do PDF e o texto completo."""
    pdf_stream = BytesIO(pdf_bytes)
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    # Primeira página tem o cabeçalho com data
    first_page = doc[0]
    full_text = first_page.get_text()
    
    # Procura data no formato "Fortaleza, DD de mês de YYYY"
    date_match = re.search(r"Fortaleza,\s*(\d{1,2})\s+de\s*(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s*(\d{4})", full_text, re.IGNORECASE)
    
    pdf_date = None
    if date_match:
        dia = int(date_match.group(1))
        mes_nome = date_match.group(2).lower()
        ano = int(date_match.group(3))
        
        meses = {
            'janeiro': 1, 'fevereiro': 2, 'março': 3, 'abril': 4,
            'maio': 5, 'junho': 6, 'julho': 7, 'agosto': 8,
            'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12
        }
        mes = meses.get(mes_nome)
        if mes:
            pdf_date = datetime.date(ano, mes, dia)
            print(f"Data encontrada no PDF: {pdf_date}")
    
    # Texto completo de todas as páginas
    full_text_pages = []
    for page in doc:
        full_text_pages.append(page.get_text())
    doc.close()
    
    return pdf_date, "\n".join(full_text_pages)


def search_names_in_text(text, names):
    text_lower = text.lower()
    found = []
    for name in names:
        if name.lower() in text_lower:
            found.append(name)
    return found


def send_email(subject: str, body: str):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("Configuração de SMTP ausente.")
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
        print("✅ E-mail enviado!")
    except Exception as e:
        print(f"❌ Erro ao enviar e-mail: {e}")


def main():
    print("=== Bot DOE/CE iniciado ===")
    state = load_state()
    if already_found_today(state):
        print("❌ Já encontrou hoje. Parando.")
        return

    today = datetime.date.today()
    diario_hoje_publicado = get_last_published_date()
    
    if not diario_hoje_publicado:
        print("⏳ Diário de hoje ainda não publicado.")
        return

    print("📥 Baixando PDF de hoje...")
    try:
        url = build_pdf_url_for_date(today)
        pdf_bytes = download_pdf_bytes(url)
    except Exception as e:
        print(f"❌ Erro ao baixar PDF: {e}")
        return

    print("🔍 Extraindo texto e data do PDF...")
    try:
        pdf_date, full_text = extract_pdf_date_and_text(pdf_bytes)
    except Exception as e:
        print(f"❌ Erro ao processar PDF: {e}")
        return

    # VERIFICAÇÃO CRUCIAL: PDF é mesmo de hoje?
    if pdf_date != today:
        print(f"⚠️  PDF é de {pdf_date} (não hoje {today}). Ignorando.")
        return

    found_names = search_names_in_text(full_text, NAMES_TO_SEARCH)

    if found_names:
        today_br = today.strftime("%d/%m/%Y")
        subject = f"[BOT DOE/CE] Nome(s) no Diário Oficial - {today_br}"
        lines = [
            f"✅ Correspondência encontrada no DOE/CE em {today_br}:",
            "",
            "Nomes:",
        ] + [f"  - {name}" for name in found_names] + [
            "",
            f"📄 PDF: {url}",
            f"📅 Data confirmada: {today_br}"
        ]
        body = "\n".join(lines)

        send_email(subject, body)
        mark_found_today(state)
        print("🎉 NOME ENCONTRADO! E-mail enviado.")
    else:
        print("❌ Nomes não encontrados no PDF de hoje.")


if __name__ == "__main__":
    main()
