import streamlit as st
import psycopg2
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import os
import hashlib
import re

# Cargar variables de entorno primero
from dotenv import load_dotenv
load_dotenv()

# ============================================================================
# FUNCIÓN HELPER PARA FECHA/HORA DE BUENOS AIRES
# ============================================================================
def ahora_buenos_aires():
    """Retorna la fecha/hora actual en zona horaria de Buenos Aires (Argentina)
    Sin información de timezone para compatibilidad con TIMESTAMP en PostgreSQL"""
    return datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).replace(tzinfo=None)

# Importar módulos necesarios al inicio
import cloudinary
import cloudinary.uploader
import cloudinary.api
from email_validator import validate_email, EmailNotValidError
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import json
from pathlib import Path
import time
from sqlalchemy import create_engine

# Lazy imports - solo cargar cuando se necesiten
def lazy_import_reportlab():
    """Importar ReportLab solo cuando se genere PDF"""
    global letter, getSampleStyleSheet, ParagraphStyle, inch
    global SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, colors
    
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    
    return True



# ============================================================================
# OPTIMIZACIÓN CRÍTICA PARA STREAMLIT CLOUD
# ============================================================================

@st.cache_resource(ttl=3600)  # Cache por 1 hora
def get_db_pool():
    """Pool de conexiones persistente"""
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5
    )

@st.cache_resource
def init_cloudinary():
    """Inicializar Cloudinary una sola vez"""
    import cloudinary
    cloudinary.config(
        cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
        api_key=os.getenv('CLOUDINARY_API_KEY'),
        api_secret=os.getenv('CLOUDINARY_API_SECRET'),
        secure=True
    )
    return True

# Inicializar al arranque
init_cloudinary()

@st.cache_data(ttl=600)  # Cache 10 minutos
def get_opciones_formulario():
    """Cachear listas estáticas"""
    return {
        'tipos': TIPOS_EQUIPO,
        'marcas': MARCAS_EQUIPO,
        'modelos': MODELOS_EQUIPO,
        'comerciales': COMERCIALES,
        'solicitantes': SOLICITANTES_INTERNOS,
        'fallas': FALLAS_PROBLEMAS
    }

def validar_solo_numeros(texto):
    """Filtra el texto para que solo contenga números"""
    if not texto:
        return ""
    return ''.join(filter(str.isdigit, texto))

# ============================================================================
# CONFIGURACIÓN DE SEGURIDAD
# ============================================================================

# Rate Limiting
MAX_SOLICITUDES_POR_HORA = 5  # Máximo 5 solicitudes por hora por usuario
VENTANA_RATE_LIMIT_MINUTOS = 60

# Tamaños de archivo
TAMANO_MAX_IMAGEN_MB = 10
TAMANO_MAX_VIDEO_MB = 50
TAMANO_MAX_DOCUMENTO_MB = 5

# Límites de texto
MAX_LENGTH_TEXTO_CORTO = 255
MAX_LENGTH_TEXTO_LARGO = 2000

# Extensiones permitidas
EXTENSIONES_IMAGENES = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
EXTENSIONES_VIDEOS = ['.mp4', '.mov', '.avi', '.mkv']
EXTENSIONES_DOCUMENTOS = ['.pdf']


# Intentar importar magic (opcional pero recomendado)
try:
    import magic
    MAGIC_DISPONIBLE = True
except ImportError:
    MAGIC_DISPONIBLE = False
    print("⚠️ python-magic no instalado. Validación de MIME type deshabilitada.")



st.set_page_config(
    page_title="Solicitud de Servicio Técnico - Syemed",
    page_icon="https://res.cloudinary.com/dfxjqvan0/image/upload/v1762455374/LOGO_MUX-removebg-preview_ms8w9o.png",  
    layout="wide"
)

# Configuración de base de datos
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    st.error("❌ Error: DATABASE_URL no configurada")
    st.stop()

def subir_archivo_cloudinary(archivo, carpeta="solicitudes_st"):
    """
    Sube un archivo a Cloudinary y retorna la URL
    
    Args:
        archivo: El archivo subido por Streamlit (UploadedFile)
        carpeta: Carpeta en Cloudinary donde se guardará
    
    Returns:
        tuple: (exito: bool, url_o_mensaje: str)
    """
    try:
        # Verificar configuración
        if not cloudinary.config().cloud_name:
            return False, "Cloudinary no está configurado. Verifica las variables de entorno."
        
        timestamp = ahora_buenos_aires().strftime("%Y%m%d_%H%M%S")
        # Sanitizar nombre: quitar espacios, &, y caracteres especiales
        nombre_limpio = re.sub(r'[^\w\-.]', '_', archivo.name)
        nombre_archivo = f"{timestamp}_{nombre_limpio}"
        
        # Determinar el tipo de archivo y resource_type
        extension = archivo.name.lower().split('.')[-1]
        if extension == 'pdf':
            resource_type = "raw"  # ⚠️ CRÍTICO para PDFs
        elif extension in ['mp4', 'mov', 'avi', 'mkv', 'webm']:
            resource_type = "video"
        else:
            resource_type = "image"
        
        # Debug: mostrar info
        #st.info(f"🔄 Subiendo: {archivo.name} ({archivo.size} bytes)")
        
        resultado = cloudinary.uploader.upload(
            archivo,
            folder=carpeta,
            public_id=nombre_archivo,
            resource_type=resource_type,
            overwrite=True,
            tags=["solicitud_st", timestamp]
        )
        
        #st.success(f"✅ Subido a Cloudinary: {resultado['secure_url'][:50]}...")
        return True, resultado['secure_url']
        
    except Exception as e:
        error_msg = f"Error al subir archivo: {str(e)}"
        st.error(f"❌ {error_msg}")
        return False, error_msg

def subir_pdf_bytes_cloudinary(pdf_bytes, nombre_archivo, carpeta="solicitudes_st/pdfs"):
    """
    Sube un PDF desde bytes a Cloudinary
    
    Args:
        pdf_bytes: El PDF en formato bytes
        nombre_archivo: Nombre para el archivo (sin extensión)
        carpeta: Carpeta en Cloudinary
    
    Returns:
        tuple: (exito: bool, url_o_mensaje: str)
    """
    try:
        # Verificar configuración
        if not cloudinary.config().cloud_name:
            return False, "Cloudinary no está configurado. Verifica las variables de entorno."
        
        # Crear buffer de bytes y posicionarlo al inicio
        pdf_buffer = io.BytesIO(pdf_bytes)
        pdf_buffer.seek(0)  # ← CRÍTICO: Asegurar que el buffer esté al inicio
        
        # Subir a Cloudinary
        resultado = cloudinary.uploader.upload(
            pdf_buffer,
            folder=carpeta,
            public_id=nombre_archivo,
            resource_type="raw",  # CRÍTICO para PDFs
            format="pdf",
            overwrite=True,
            tags=["solicitud_pdf", ahora_buenos_aires().strftime("%Y%m%d")]
        )
        
        return True, resultado['secure_url']
        
    except cloudinary.exceptions.Error as e:
        error_msg = f"Error de Cloudinary al subir PDF: {str(e)}"
        return False, error_msg
    except Exception as e:
        error_msg = f"Error general al subir PDF: {str(e)}"
        return False, error_msg
    except Exception as e:
        error_msg = f"Error general al subir PDF: {str(e)}"
        return False, error_msg
    
def subir_multiples_archivos_cloudinary(archivos, carpeta="solicitudes_st"):
    """Sube múltiples archivos a Cloudinary"""
    urls = []
    errores = []
    
    for archivo in archivos:
        exito, resultado = subir_archivo_cloudinary(archivo, carpeta)
        if exito:
            urls.append({
                'nombre': archivo.name,
                'url': resultado,
                'tipo': archivo.type
            })
        else:
            errores.append({'nombre': archivo.name, 'error': resultado})
    
    return (True, urls) if not errores else (False, errores)

# Crear engine de SQLAlchemy para pandas
def get_sqlalchemy_engine():
    return create_engine(DATABASE_URL)

# CSS personalizado
st.markdown("""
<style>
    .main-header {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .section-header {
        background-color: #e8f4f8;
        padding: 10px;
        border-radius: 5px;
        margin: 20px 0 10px 0;
        border-left: 4px solid #1f77b4;
    }
    .equipment-section {
        background-color: #f9f9f9;
        padding: 15px;
        border-radius: 8px;
        margin: 10px 0;
        border: 1px solid #ddd;
    }
    .error-box {
        background-color: #ffebee;
        border-left: 4px solid #f44336;
        padding: 15px;
        border-radius: 5px;
        margin: 10px 0;
    }
</style>
""", unsafe_allow_html=True)

# Listas de opciones
TIPOS_EQUIPO = [
    "Seleccionar tipo...",
    "Analizador de gases", "Asistente de Tos", "Aspirador de secreciones", 
    "Aspirador Manual", "Balón de Contrapulsación", "Bomba a jeringa", 
    "Bomba de Infusión", "Bomba de Presión Negativa", "BPAP", "Cables Varios",
    "Calentador Humidificador", "Capnógrafo", "Cardiodesfibrilador", 
    "Concentrador de Oxígeno", "Concentrador de Oxígeno Portátil", "CPAP",
    "DEA", "Electrocardiógrafo", "Incubadora", "Luminoterapia", "Marcapasos",
    "Mesa de Anestesia", "Mochila de Oxígeno", "Módulo de Capnografía",
    "Módulo PI", "Monitor Multiparamétrico", "Oxímetro de Pulso", "Respirador",
    "Respirador Portátil", "Tubo de Oxígeno", "Vaporizador de anestesia",
    "No se/No lo encuentro en la lista"
]

MARCAS_EQUIPO = [
    "Seleccionar marca...",
    "Arrow", "Biocare", "Bistos", "Cardiotécnica", "Cegens", "Comen",
    "Confort Cough", "Contec", "Covidien", "Daiwha", "Datascope", "Dräger",
    "Edan", "Enmind", "Fisher&Paykel", "Leex", "Lifotronic", "Long Fian",
    "Lovego", "Marbel", "Massimo", "Maverick", "MDV", "Medix", "Medtronic",
    "Mindray", "MUX", "Nellcor", "Neumovent", "Philips", "Yuwell",
    "No se / No lo encuentro en esta lista"
]

MODELOS_EQUIPO = [
    "Seleccionar modelo...",
    "7E-C", "7E-G", "7F-10", "7F-5 Mini", "9F-5", "Autocat II", "Autocat II Wave",
    "BT-400", "BT-500", "Cloud", "CC20", "CMS8000", "CO2-M01", "DI2000",
    "EN-S7", "EN-V7", "Evergo", "Fabius", "Fabius Plus", "Fabius Plus XL",
    "Graphnet TS", "HC100", "HT-109", "iE-101", "iE-300", "IM8B", "Jay-5",
    "Jay-5Q", "LG103", "Libra", "M3A", "MR810", "N/E", "NP-100", "NP-600",
    "Prisma Vent 40", "Prisma Vent 50", "Puritan Bennett 560", "RG-401",
    "RG-401 Plus", "RG-501", "RG-501 Plus", "Scio Four", "SP-50", "SP-50 Pro",
    "Spirit 3", "Star 8000", "System 97", "System 97e", "Trilogy", "Vapor 2000",
    "Vista 120", "VP-50", "VP-50 Pro", "YH-350", "YH-360", "YH-550", "YH-560",
    "YH-725", "YH-730", "5342", "5346", "No se / No lo encuentro en esta lista"
]

COMERCIALES = ["Seleccionar comercial...", "Ariel", "Clara", "Diana", "Francesca", "Isabel", "Lucas", "Miguel"]
SOLICITANTES_INTERNOS = ["Seleccionar solicitante...", "Ariel",  "Clara", "Daiana", "Diana", "Facundo", "Francesca", "Isabel", "Lucas", "Miguel", "Rubén", "Tomás"]

FALLAS_PROBLEMAS = [
    "El equipo no muestra ningún signo de falla pero no funciona",
    "El equipo no enciende cuando lo enchufo",
    "El equipo presento una falla en su funcionamiento",
    "El equipo indica un código de error",
    "El equipo se cayo y no funciona",
    "El equipo se mojó y no funciona",
    "El equipo muestra una alarma amarilla/roja",
    "Faltan accesorios",
    "Garantia",
    "No se como se usa el equipamiento",
    "No se como funcionan los descartables del equipo"
]

# Mapeo de texto de Post Venta a Asistencia Técnica (para mostrar al usuario)
TEXTO_POST_VENTA_INTERNO = "Servicio Post Venta (para alguno de nuestros productos adquiridos)"
TEXTO_ASISTENCIA_TECNICA_DISPLAY = "Servicio de Asistencia Técnica (para nuestros productos adquiridos)"

# Función para convertir texto display a valor interno
def normalizar_motivo_solicitud(motivo_display):
    """Convierte el texto mostrado al usuario al valor interno de BD"""
    if motivo_display == TEXTO_ASISTENCIA_TECNICA_DISPLAY:
        return TEXTO_POST_VENTA_INTERNO
    return motivo_display

def formatear_motivo_solicitud_display(motivo_interno):
    """Convierte el texto interno de BD al texto para mostrar en PDF"""
    if motivo_interno == TEXTO_POST_VENTA_INTERNO:
        return "Asistencia Técnica"
    return motivo_interno

def validar_email_formato(email):
    """
    Valida el formato del email usando email-validator
    Retorna: (es_valido: bool, mensaje: str, email_normalizado: str)
    """
    if not email or not email.strip():
        return False, "El email es requerido", ""
    
    try:
        valid = validate_email(email, check_deliverability=False)
        return True, "Email válido", valid.normalized
    except EmailNotValidError as e:
        return False, str(e), ""

def validar_campos_obligatorios(data):
    """
    Valida todos los campos obligatorios según el tipo de solicitante
    Retorna: (es_valido: bool, lista_errores: list)
    """
    errores = []
    
    # Validaciones comunes
    if not data.get('email'):
        errores.append("El correo electrónico es obligatorio")
    
    if not data.get('quien_completa'):
        errores.append("Debe indicar quién completa la solicitud")
    
    quien_completa = data.get('quien_completa', '')
    
    # Validaciones para Colaborador de Syemed
    if quien_completa == "Colaborador de Syemed":
        if not data.get('area_solicitante'):
            errores.append("Área Solicitante es obligatorio")
        if not data.get('solicitante') or data.get('solicitante') == "Seleccionar solicitante...":
            errores.append("Solicitante es obligatorio")
        if not data.get('equipo_corresponde_a'):
            errores.append("'El equipo corresponde a' es obligatorio")
        
        equipo_corresponde_a = data.get('equipo_corresponde_a', '')
        
        # Validaciones según a quién corresponde el equipo
        if equipo_corresponde_a == "Distribuidor":
            if not data.get('nombre_fantasia'):
                errores.append("Nombre de Fantasía (Distribuidor) es obligatorio")
            if not data.get('razon_social'):
                errores.append("Razón Social (Distribuidor) es obligatorio")
            if not data.get('cuit'):
                errores.append("CUIT (Distribuidor) es obligatorio")
            if not data.get('contacto_nombre'):
                errores.append("Nombre de contacto (Distribuidor) es obligatorio")
            if not data.get('contacto_telefono'):
                errores.append("Teléfono de contacto (Distribuidor) es obligatorio")
            if not data.get('contacto_tecnico'):
                errores.append("Debe indicar si quiere contacto técnico (Distribuidor)")
            if not data.get('motivo_solicitud'):
                errores.append("Motivo de la solicitud (Distribuidor) es obligatorio")
        
        elif equipo_corresponde_a == "Institución":
            if not data.get('nombre_fantasia'):
                errores.append("Nombre del Hospital/Clínica (Institución) es obligatorio")
            if not data.get('razon_social'):
                errores.append("Razón Social (Institución) es obligatorio")
            if not data.get('contacto_nombre'):
                errores.append("Nombre de contacto (Institución) es obligatorio")
            if not data.get('contacto_telefono'):
                errores.append("Teléfono de contacto (Institución) es obligatorio")
            if not data.get('contacto_tecnico'):
                errores.append("Debe indicar si quiere contacto técnico (Institución)")
            if not data.get('motivo_solicitud'):
                errores.append("Motivo de la solicitud (Institución) es obligatorio")
        
        elif equipo_corresponde_a == "Paciente/Particular":
            if not data.get('nombre_apellido_paciente'):
                errores.append("Nombre y Apellido (Paciente) es obligatorio")
            if not data.get('telefono_paciente'):
                errores.append("Teléfono (Paciente) es obligatorio")
            if not data.get('equipo_origen'):
                errores.append("Origen del equipo (Paciente) es obligatorio")
            if not data.get('motivo_solicitud'):
                errores.append("Motivo de la solicitud (Paciente) es obligatorio")
    
    # Validaciones para Distribuidor directo
    elif quien_completa == "Distribuidor":
        if not data.get('nombre_fantasia'):
            errores.append("Nombre de Fantasía es obligatorio")
        if not data.get('razon_social'):
            errores.append("Razón Social es obligatorio")
        if not data.get('cuit'):
            errores.append("CUIT es obligatorio")
        if not data.get('contacto_nombre'):
            errores.append("Nombre de contacto es obligatorio")
        if not data.get('contacto_telefono'):
            errores.append("Teléfono de contacto es obligatorio")
        if not data.get('comercial_syemed') or data.get('comercial_syemed') == "Seleccionar comercial...":
            errores.append("Comercial de contacto en Syemed es obligatorio")
        if not data.get('contacto_tecnico'):
            errores.append("Debe indicar si quiere contacto técnico")
        if not data.get('motivo_solicitud'):
            errores.append("Motivo de la solicitud es obligatorio")
    
    # Validaciones para Institución directa
    elif quien_completa == "Institución":
        if not data.get('nombre_fantasia'):
            errores.append("Nombre del Hospital/Clínica/Sanatorio es obligatorio")
        if not data.get('razon_social'):
            errores.append("Razón Social es obligatorio")
        if not data.get('contacto_nombre'):
            errores.append("Nombre de contacto es obligatorio")
        if not data.get('contacto_telefono'):
            errores.append("Teléfono de contacto es obligatorio")
        if not data.get('comercial_syemed') or data.get('comercial_syemed') == "Seleccionar comercial...":
            errores.append("Comercial de contacto en Syemed es obligatorio")
        if not data.get('contacto_tecnico'):
            errores.append("Debe indicar si quiere contacto técnico")
        if not data.get('motivo_solicitud'):
            errores.append("Motivo de la solicitud es obligatorio")
    
    # Validaciones para Paciente/Particular directo
    elif quien_completa == "Paciente/Particular":
        if not data.get('nombre_apellido_paciente'):
            errores.append("Nombre y Apellido es obligatorio")
        if not data.get('telefono_paciente'):
            errores.append("Teléfono de contacto es obligatorio")
        if not data.get('equipo_origen'):
            errores.append("Origen del equipo es obligatorio")
        if not data.get('motivo_solicitud'):
            errores.append("Motivo de la solicitud es obligatorio")
    
    # Validaciones según motivo de solicitud
    motivo = data.get('motivo_solicitud', '')
    
    if motivo == "Cambio de Alquiler":
        if not data.get('motivo_cambio_alquiler', '').strip():
            errores.append("Debe especificar el motivo del cambio de alquiler")
    
    elif motivo == "Cambio por falla de funcionamiento crítica":
        if not data.get('detalle_fallo', '').strip():
            errores.append("Debe describir la falla crítica que justifica el cambio")
    
    elif motivo in ["Servicio Técnico (reparaciones de equipos en general)", 
                    "Servicio Post Venta (para alguno de nuestros productos adquiridos)"]:
        fallas = data.get('fallas_problemas', [])
        detalle = data.get('detalle_fallo', '')
        
        if not fallas and not detalle.strip():
            tipo_req = "fallas" if "Técnico" in motivo else "consultas"
            errores.append(f"Debe seleccionar al menos una opción o especificar en 'Otros' el motivo de su solicitud")
    
    # Validaciones de equipos
    equipos_validos = [
        eq for eq in data.get('equipos', []) 
        if eq.get('tipo_equipo') and eq.get('tipo_equipo') != "Seleccionar tipo..."
    ]
    
    if not equipos_validos:
        errores.append("Debe registrar al menos un equipo")
    else:
        for i, equipo in enumerate(equipos_validos, 1):
            if not equipo.get('marca') or equipo.get('marca') == "Seleccionar marca...":
                errores.append(f"La marca del equipo {i} es obligatoria")
            if not equipo.get('modelo') or equipo.get('modelo') == "Seleccionar modelo...":
                errores.append(f"El modelo del equipo {i} es obligatorio")
            if not equipo.get('numero_serie'):
                errores.append(f"El número de serie del equipo {i} es obligatorio")
            #if not equipo.get('en_garantia'):
                #errores.append(f"Debe indicar si el equipo {i} está en garantía")
    
    return len(errores) == 0, errores

def generar_pdf_solicitud(data, solicitud_id, equipos_osts=None):
    """
    Genera un PDF con el resumen completo de la solicitud organizado por categorías
    Retorna: bytes del PDF
    """

    # Importar ReportLab solo cuando se necesite
    lazy_import_reportlab()
    
    
    buffer = io.BytesIO()
    # Determinar OST principal para el título
    ost_principal = equipos_osts[0] if equipos_osts else solicitud_id
    
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter, 
        rightMargin=72, 
        leftMargin=72, 
        topMargin=72, 
        bottomMargin=18,
        title=f"Solicitud ST - OST #{ost_principal}",  
        author="Syemed - Asistencia Técnica y ST",     
        subject=f"Solicitud de Servicio Técnico - OST #{ost_principal}"  
    )
    
    elementos = []
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        'CustomTitle',
        parent=estilos['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1f77b4'),
        spaceAfter=30,
        alignment=1
    )
    estilo_subtitulo = ParagraphStyle(
        'CustomSubtitle',
        parent=estilos['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#333333'),
        spaceAfter=12,
        spaceBefore=12
    )
    estilo_normal = estilos['Normal']
    
    # Título
    elementos.append(Paragraph(f"Solicitud de Servicio Técnico - Caso #{solicitud_id}", estilo_titulo))
    elementos.append(Paragraph(f"Fecha: {ahora_buenos_aires().strftime('%d/%m/%Y %H:%M')}", estilo_normal))
    elementos.append(Spacer(1, 0.3*inch))
    
    # ====================================================================================
    # SECCIÓN 1: INFORMACIÓN SEGÚN TIPO DE SOLICITANTE
    # ====================================================================================
    quien_completa = data.get('quien_completa', '')
    
    elementos.append(Paragraph("INFORMACIÓN DE LA SOLICITUD", estilo_subtitulo))
    
    info_general = [
        ["Correo electrónico:", data.get('email', 'N/A')],
        ["Tipo de solicitante:", quien_completa or 'N/A'],
    ]
    
    # ========== COLABORADOR DE SYEMED ==========
    if quien_completa == "Colaborador de Syemed":
        # Mapear nivel de urgencia numérico a texto
        nivel_urgencia_num = data.get('nivel_urgencia', 0)
        if nivel_urgencia_num <= 1:
            nivel_urgencia_texto = f"Bajo ({nivel_urgencia_num})"
        elif 1 < nivel_urgencia_num <= 3:
            nivel_urgencia_texto = f"Medio ({nivel_urgencia_num})"
        else:  # 4-5
            nivel_urgencia_texto = f"Alto ({nivel_urgencia_num})"
        
        info_general.extend([
            ["Área solicitante:", data.get('area_solicitante', 'N/A')],
            ["Solicitante:", data.get('solicitante', 'N/A')],
            ["Nivel de Urgencia:", nivel_urgencia_texto],
            ["Logística a cargo:", data.get('logistica_cargo', 'N/A')],
            ["Comentarios del caso:", data.get('comentarios_caso', 'N/A')],
        ])
        
        tabla_info = Table(info_general, colWidths=[2*inch, 4*inch])
        tabla_info.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f4f8')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        elementos.append(tabla_info)
        elementos.append(Spacer(1, 0.2*inch))
        
        # Subsección: El equipo corresponde a...
        equipo_corresponde_a = data.get('equipo_corresponde_a', '')
        elementos.append(Paragraph(f"<b>El equipo corresponde a: {equipo_corresponde_a}</b>", estilo_normal))
        elementos.append(Spacer(1, 0.1*inch))
        
        info_equipo_corresponde = []
        
        if equipo_corresponde_a in ["Distribuidor", "Institución"]:
            info_equipo_corresponde.extend([
                ["Nombre de fantasía:", data.get('nombre_fantasia', 'N/A')],
                ["Razón social:", data.get('razon_social', 'N/A')],
                ["CUIT:", data.get('cuit', 'N/A')],
                ["Nombre contacto:", data.get('contacto_nombre', 'N/A')],
                ["Teléfono:", data.get('contacto_telefono', 'N/A')],
                ["Comercial a cargo:", data.get('comercial_syemed', 'N/A')],
                ["¿Lo contactamos?:", data.get('contacto_tecnico', 'N/A')],
                ["Motivo solicitud:", formatear_motivo_solicitud_display(data.get('motivo_solicitud', 'N/A'))],
                ["Propio o Alquilado:", data.get('equipo_propiedad', 'N/A')],
            ])
            
        elif equipo_corresponde_a == "Paciente/Particular":
            info_equipo_corresponde.extend([
                ["Nombre y Apellido:", data.get('nombre_apellido_paciente', 'N/A')],
                ["Teléfono:", data.get('telefono_paciente', 'N/A')],
                ["Dirección:", data.get('direccion_paciente', 'N/A')],
                ["¿Lo contactamos?:", data.get('contacto_tecnico', 'N/A')],
                ["Motivo solicitud:", formatear_motivo_solicitud_display(data.get('motivo_solicitud', 'N/A'))],
                ["Diagnóstico del Paciente:", data.get('diagnostico_paciente', 'N/A')],
            ])
        
        if info_equipo_corresponde:
            tabla_corresponde = Table(info_equipo_corresponde, colWidths=[2*inch, 4*inch])
            tabla_corresponde.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#fff4e6')),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey)
            ]))
            elementos.append(tabla_corresponde)
    
    # ========== DISTRIBUIDOR ==========
    elif quien_completa == "Distribuidor":
        info_general.extend([
            ["Nombre de fantasía:", data.get('nombre_fantasia', 'N/A')],
            ["Razón social:", data.get('razon_social', 'N/A')],
            ["CUIT:", data.get('cuit', 'N/A')],
            ["Nombre contacto:", data.get('contacto_nombre', 'N/A')],
            ["Teléfono:", data.get('contacto_telefono', 'N/A')],
            ["Comercial a cargo:", data.get('comercial_syemed', 'N/A')],
            ["¿Lo contactamos?:", data.get('contacto_tecnico', 'N/A')],
            ["Motivo solicitud:", formatear_motivo_solicitud_display(data.get('motivo_solicitud', 'N/A'))],
            ["Propio o Alquilado:", data.get('equipo_propiedad', 'N/A')],
        ])
        
        tabla_info = Table(info_general, colWidths=[2*inch, 4*inch])
        tabla_info.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f4f8')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        elementos.append(tabla_info)
    
    # ========== INSTITUCIÓN ==========
    elif quien_completa == "Institución":
        info_general.extend([
            ["Nombre de fantasía:", data.get('nombre_fantasia', 'N/A')],
            ["Razón social:", data.get('razon_social', 'N/A')],
            ["CUIT:", data.get('cuit', 'N/A')],
            ["Nombre contacto:", data.get('contacto_nombre', 'N/A')],
            ["Teléfono:", data.get('contacto_telefono', 'N/A')],
            ["Comercial a cargo:", data.get('comercial_syemed', 'N/A')],
            ["¿Lo contactamos?:", data.get('contacto_tecnico', 'N/A')],
            ["Motivo solicitud:", formatear_motivo_solicitud_display(data.get('motivo_solicitud', 'N/A'))],
            ["Propio o Alquilado:", data.get('equipo_propiedad', 'N/A')],
        ])
        
        tabla_info = Table(info_general, colWidths=[2*inch, 4*inch])
        tabla_info.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f4f8')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        elementos.append(tabla_info)
    
    # ========== PACIENTE/PARTICULAR ==========
    elif quien_completa == "Paciente/Particular":
        info_general.extend([
            ["Nombre y Apellido:", data.get('nombre_apellido_paciente', 'N/A')],
            ["Teléfono:", data.get('telefono_paciente', 'N/A')],
            ["Dirección:", data.get('direccion_paciente', 'N/A')],
            ["¿Lo contactamos?:", data.get('contacto_tecnico', 'N/A')],
            ["Motivo solicitud:", formatear_motivo_solicitud_display(data.get('motivo_solicitud', 'N/A'))],
        ])
        
        tabla_info = Table(info_general, colWidths=[2*inch, 4*inch])
        tabla_info.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f4f8')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        elementos.append(tabla_info)
    
    elementos.append(Spacer(1, 0.3*inch))
    
    # ====================================================================================
    # SECCIÓN 2: DETALLES TÉCNICOS
    # ====================================================================================
    motivo_solicitud = data.get('motivo_solicitud', '')
    
    # Determinar si hay información técnica que mostrar
    tiene_info_tecnica = False
    detalle_observacion = ""
    
    if motivo_solicitud == "Servicio Técnico (reparaciones de equipos en general)":
        # Para ST: fallas + detalle + diagnóstico
        tiene_info_tecnica = data.get('fallas_problemas') or data.get('detalle_fallo') or data.get('diagnostico_paciente')
        partes = []
        fallas = data.get('fallas_problemas', [])
        if fallas:
            partes.append(', '.join(fallas))
        detalle = data.get('detalle_fallo', '')
        if detalle:
            partes.append(detalle)
        diagnostico = data.get('diagnostico_paciente', '')
        if diagnostico:
            partes.append(f"Diagnóstico: {diagnostico}")
        if partes:
            detalle_observacion = ' | '.join(partes)
    
    elif motivo_solicitud == "Servicio Post Venta (para alguno de nuestros productos adquiridos)":
        # Para Asistencia Técnica: consultas + detalle + diagnóstico
        tiene_info_tecnica = data.get('fallas_problemas') or data.get('detalle_fallo') or data.get('diagnostico_paciente')
        partes = []
        fallas = data.get('fallas_problemas', [])
        if fallas:
            partes.append(', '.join(fallas))
        detalle = data.get('detalle_fallo', '')
        if detalle:
            partes.append(detalle)
        diagnostico = data.get('diagnostico_paciente', '')
        if diagnostico:
            partes.append(f"Diagnóstico: {diagnostico}")
        if partes:
            detalle_observacion = ' | '.join(partes)
    
    elif motivo_solicitud == "Baja de Alquiler":
        # Para Baja de Alquiler: motivo + observación + estado
        tiene_info_tecnica = data.get('motivo_baja') or data.get('observacion_baja') or data.get('estado_equipo')
        partes = []
        motivo_baja = data.get('motivo_baja', '')
        if motivo_baja:
            partes.append(f"Motivo: {motivo_baja}")
        observacion_baja = data.get('observacion_baja', '')
        if observacion_baja:
            partes.append(observacion_baja)
        estado_equipo = data.get('estado_equipo', '')
        if estado_equipo:
            partes.append(f"Estado: {estado_equipo}")
        if partes:
            detalle_observacion = ' | '.join(partes)
    
    elif motivo_solicitud == "Cambio de Alquiler":
        # Para Cambio de Alquiler: motivo del cambio
        tiene_info_tecnica = data.get('motivo_cambio_alquiler')
        detalle_observacion = data.get('motivo_cambio_alquiler', '')
    
    elif motivo_solicitud == "Cambio por falla de funcionamiento crítica":
        # Para Falla Crítica: descripción + diagnóstico
        tiene_info_tecnica = data.get('detalle_fallo') or data.get('diagnostico_paciente')
        partes = []
        detalle = data.get('detalle_fallo', '')
        if detalle:
            partes.append(detalle)
        diagnostico = data.get('diagnostico_paciente', '')
        if diagnostico:
            partes.append(f"Diagnóstico: {diagnostico}")
        if partes:
            detalle_observacion = ' | '.join(partes)
    
    # Mostrar sección DETALLES TÉCNICOS si hay información
    if tiene_info_tecnica:
        elementos.append(Paragraph("DETALLES TÉCNICOS", estilo_subtitulo))
        
        # Para ST y Asistencia Técnica: mostrar fallas seleccionadas
        if motivo_solicitud in ["Servicio Técnico (reparaciones de equipos en general)", 
                                "Servicio Post Venta (para alguno de nuestros productos adquiridos)"]:
            if data.get('fallas_problemas'):
                elementos.append(Paragraph("<b>Fallas detectadas seleccionadas:</b>", estilo_normal))
                for falla in data.get('fallas_problemas', []):
                    elementos.append(Paragraph(f"• {falla}", estilo_normal))
                elementos.append(Spacer(1, 0.1*inch))
            
            if data.get('detalle_fallo'):
                elementos.append(Paragraph("<b>Otros problemas o detalles adicionales:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('detalle_fallo', ''), estilo_normal))
                elementos.append(Spacer(1, 0.1*inch))
            
            if data.get('diagnostico_paciente'):
                elementos.append(Paragraph("<b>Diagnóstico del Paciente:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('diagnostico_paciente', ''), estilo_normal))
        
        # Para Baja de Alquiler
        elif motivo_solicitud == "Baja de Alquiler":
            if data.get('motivo_baja'):
                elementos.append(Paragraph(f"<b>Motivo de baja:</b> {data.get('motivo_baja', '')}", estilo_normal))
                elementos.append(Spacer(1, 0.05*inch))
            if data.get('observacion_baja'):
                elementos.append(Paragraph("<b>Observación:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('observacion_baja', ''), estilo_normal))
                elementos.append(Spacer(1, 0.05*inch))
            if data.get('estado_equipo'):
                elementos.append(Paragraph(f"<b>Estado del equipo:</b> {data.get('estado_equipo', '')}", estilo_normal))
        
        # Para Cambio de Alquiler
        elif motivo_solicitud == "Cambio de Alquiler":
            if data.get('motivo_cambio_alquiler'):
                elementos.append(Paragraph("<b>Motivo del cambio:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('motivo_cambio_alquiler', ''), estilo_normal))
        
        # Para Falla Crítica
        elif motivo_solicitud == "Cambio por falla de funcionamiento crítica":
            if data.get('detalle_fallo'):
                elementos.append(Paragraph("<b>Descripción de la falla crítica:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('detalle_fallo', ''), estilo_normal))
                elementos.append(Spacer(1, 0.1*inch))
            if data.get('diagnostico_paciente'):
                elementos.append(Paragraph("<b>Diagnóstico del Paciente:</b>", estilo_normal))
                elementos.append(Paragraph(data.get('diagnostico_paciente', ''), estilo_normal))
        
        elementos.append(Spacer(1, 0.3*inch))
    
    # ====================================================================================
    # SECCIÓN 3: EQUIPOS REGISTRADOS
    # ====================================================================================
    elementos.append(Paragraph("EQUIPOS REGISTRADOS", estilo_subtitulo))
    
    equipos_data = [["OST", "Tipo", "Marca", "Modelo", "N° Serie", "Garantía"]]
    
    for i, equipo in enumerate(data.get('equipos', []), 1):
        if equipo.get('tipo_equipo') and equipo['tipo_equipo'] != "Seleccionar tipo...":
            # Obtener el OST correspondiente a este equipo
            ost_equipo = equipos_osts[i-1] if equipos_osts and i-1 < len(equipos_osts) else "N/A"
            
            equipos_data.append([
                f"#{ost_equipo}",
                equipo.get('tipo_equipo', 'N/A'),
                equipo.get('marca', 'N/A'),
                equipo.get('modelo', 'N/A'),
                equipo.get('numero_serie', 'N/A'),
                "Sí" if equipo.get('en_garantia') else "No"
            ])
    
    tabla_equipos = Table(equipos_data, colWidths=[0.6*inch, 1.5*inch, 1.2*inch, 1.2*inch, 1.2*inch, 0.7*inch])
    tabla_equipos.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f77b4')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')])
    ]))
    elementos.append(tabla_equipos)
    
    doc.build(elementos)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    
    return pdf_bytes
def enviar_email_con_pdf(destinatario, solicitud_id, pdf_bytes, data, equipos_osts=None):
    """
    Envía un email con el PDF adjunto usando Gmail
    """
    # Obtener credenciales desde variables de entorno
    sender_email = os.getenv('SMTP_EMAIL')
    sender_password = os.getenv('SMTP_PASSWORD')
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    email_copia = os.getenv('EMAIL_COPIA', '')
    
    # Validar que existan las credenciales
    if not sender_email or not sender_password:
        return False, "Error: Credenciales SMTP no configuradas"
    
    try:
        # Crear mensaje
        msg = MIMEMultipart()
        msg['From'] = f"Post Venta y Servicio Técnico Syemed <{sender_email}>"
        msg['To'] = destinatario
        
        # Agregar copia si está configurada
        if email_copia:
            msg['Cc'] = email_copia
        
        # Generar código de categoría para el asunto
        codigo_categoria = generar_codigo_categoria(data)
        msg['Subject'] = f"{codigo_categoria} Seguimiento Caso #{solicitud_id} - Syemed"
        
        # Determinar información del solicitante según tipo
        quien_completa = data.get('quien_completa', 'N/A')
        info_solicitante = ""
        info_telefono = ""
        info_contacto_tecnico = "" 
        
        if quien_completa == "Colaborador de Syemed":
            solicitante = data.get('solicitante', 'N/A')
            info_solicitante = f"Colaborador de Syemed: {solicitante}"
            
            # Determinar información según a quién corresponde el equipo
            equipo_corresponde_a = data.get('equipo_corresponde_a', '')
            if equipo_corresponde_a == "Distribuidor":
                nombre = data.get('nombre_fantasia', 'N/A')
                info_solicitante += f"\n- Cliente (Distribuidor): {nombre}"
                telefono = data.get('contacto_telefono', '')
                if telefono:
                    info_telefono = f"- Teléfono: {telefono}"
                    
            elif equipo_corresponde_a == "Institución":
                nombre = data.get('nombre_fantasia', 'N/A')
                info_solicitante += f"\n- Cliente (Institución): {nombre}"
                telefono = data.get('contacto_telefono', '')
                if telefono:
                    info_telefono = f"- Teléfono: {telefono}"
                    
            elif equipo_corresponde_a == "Paciente/Particular":
                nombre = data.get('nombre_apellido_paciente', 'N/A')
                info_solicitante += f"\n- Cliente (Paciente): {nombre}"
                telefono = data.get('telefono_paciente', '')
                if telefono:
                    info_telefono = f"- Teléfono: {telefono}"
        
        elif quien_completa == "Distribuidor":
            nombre = data.get('nombre_fantasia', 'N/A')
            info_solicitante = f"Distribuidor: {nombre}"
            telefono = data.get('contacto_telefono', '')
            if telefono:
                info_telefono = f"- Teléfono: {telefono}"
                
        elif quien_completa == "Institución":
            nombre = data.get('nombre_fantasia', 'N/A')
            info_solicitante = f"Institución: {nombre}"
            telefono = data.get('contacto_telefono', '')
            if telefono:
                info_telefono = f"- Teléfono: {telefono}"
                
        elif quien_completa == "Paciente/Particular":
            nombre = data.get('nombre_apellido_paciente', 'N/A')
            info_solicitante = f"Paciente/Particular: {nombre}"
            telefono = data.get('telefono_paciente', '')
            if telefono:
                info_telefono = f"- Teléfono: {telefono}"
            info_contacto_tecnico = ""
            if data.get('contacto_tecnico'):
               info_contacto_tecnico = f"- ¿Quiere que lo contactemos desde el área técnica?: {data.get('contacto_tecnico')}"
        
        num_equipos = len([eq for eq in data.get('equipos', []) 
                          if eq.get('tipo_equipo') != "Seleccionar tipo..."])
        
        # Construir información de OSTs
        info_osts = ""
        if equipos_osts:
            osts_formateados = ', '.join([f'#{ost}' for ost in equipos_osts])
            info_osts = f"- OST(s) generado(s): {osts_formateados}\n"
        
        # Construir cuerpo del email
        body = f"""Estimado/a,

Se ha registrado exitosamente su solicitud de servicio técnico.

DETALLES DE LA SOLICITUD:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- ID de Solicitud: #{solicitud_id}
{info_osts}- Solicitante: {info_solicitante}
{info_telefono}
{info_contacto_tecnico}
- Cantidad de equipos: {num_equipos}
- Fecha: {ahora_buenos_aires().strftime('%d/%m/%Y %H:%M')}

Adjunto encontrará el resumen completo de su solicitud en formato PDF.

Nos pondremos en contacto a la brevedad para coordinar el servicio.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HORARIO DE ATENCIÓN:
Lunes a Viernes de 8 a 17hs
Teléfono de urgencias: 11 2373-0278

Saludos cordiales,
Equipo de Asistencia Técnica y Servicio Técnico
Syemed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Este es un email automático. Por favor no responda a este mensaje.
"""
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Adjuntar PDF
        pdf_attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_attachment.add_header(
            'Content-Disposition', 
            'attachment', 
            filename=f'Solicitud_ST_{solicitud_id}_{ahora_buenos_aires().strftime("%Y%m%d")}.pdf'
        )
        msg.attach(pdf_attachment)
        
        # Preparar lista de destinatarios (incluye Cc)
        destinatarios = [destinatario]
        if email_copia:
            destinatarios.append(email_copia)
        
        # Conectar y enviar
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        
        return True, "Email enviado correctamente"
        
              
    except smtplib.SMTPAuthenticationError:
        return False, "Error de autenticación SMTP. Verifica tus credenciales."
    except smtplib.SMTPException as e:
        return False, f"Error SMTP: {str(e)}"
    except Exception as e:
        return False, f"Error al enviar email: {str(e)}"
    
def conectar_bd():
    """Usa pool de conexiones cacheado"""
    try:
        conn = get_db_pool()
        # Test si está viva
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Conexión muerta, limpiar cache y reintentar
        st.cache_resource.clear()
        try:
            return get_db_pool()
        except Exception as e:
            st.error(f"Error conectando a la base de datos: {e}")
            return None
    except Exception as e:
        st.error(f"Error conectando a la base de datos: {e}")
        return None

def generar_codigo_categoria(data):
    """
    Genera el código de categoría según las reglas NUEVAS:
    
    Para opciones especiales:
    * S: Equipo de Stock
    * BD: Baja de Demo
    
    Para Distribuidor/Institución:
    Si Alquilado:
      A/ST/R   -> Servicio Técnico
      A/AT     -> Asistencia Técnica  
      A/BA     -> Baja de Alquiler
      A/CA     -> Cambio de Alquiler
      A/FC     -> Cambio por Falla Crítica
    
    Si Propio con Garantía Sí:
      G/ST/R   -> Servicio Técnico
      G/AT     -> Asistencia Técnica
      G/FC     -> Cambio por Falla Crítica
    
    Si Propio con Garantía No:
      ST/R     -> Servicio Técnico
      AT       -> Asistencia Técnica
      FC       -> Cambio por Falla Crítica
    
    Para Paciente/Particular:
    Si se lo entregaron:
      ST/R     -> Servicio Técnico
      AT       -> Asistencia Técnica
      FC       -> Cambio por Falla Crítica
    
    Si lo compró con Garantía Sí:
      G/ST/R   -> Servicio Técnico
      G/AT     -> Asistencia Técnica
      G/FC     -> Cambio por Falla Crítica
    """
    motivo = data.get('motivo_solicitud', '')
    equipo_propiedad = data.get('equipo_propiedad', '')
    quien_completa = data.get('quien_completa', '')
    
    # Determinar si hay equipos en garantía
    equipos = data.get('equipos', [])
    tiene_garantia = any(equipo.get('en_garantia', False) for equipo in equipos)
    
    # Obtener el valor de en_garantia desde data (de la sección Información del Equipo)
    en_garantia_data = data.get('en_garantia', None)
    if en_garantia_data == "Sí":
        tiene_garantia = True
    elif en_garantia_data in ["No", "No lo sé"]:
        tiene_garantia = False
    
    # Mapear motivos a códigos
    if motivo == "Equipo de Stock":
        return "S"
    elif motivo == "Baja de demo":
        return "BD"
    elif motivo == "Baja de Alquiler":
        return "A/BA"
    elif motivo == "Cambio de Alquiler":
        return "A/CA"
    
    # Para Servicio Técnico, Asistencia Técnica (Post Venta), Cambio por falla crítica
    if motivo == "Servicio Técnico (reparaciones de equipos en general)":
        codigo_motivo = "ST/R"
    elif motivo == "Servicio Post Venta (para alguno de nuestros productos adquiridos)":
        codigo_motivo = "AT"
    elif motivo == "Cambio por falla de funcionamiento crítica":
        codigo_motivo = "FC"
    else:
        return "N/A"
    
    # Para Distribuidor/Institución
    if quien_completa in ["Distribuidor", "Institución", "Colaborador de Syemed"]:
        equipo_origen = data.get('equipo_origen', '')
        
        # Si es alquilado
        if equipo_propiedad == "Alquilado":
            return f"A/{codigo_motivo}"
        
        # Si es propio
        elif equipo_propiedad == "Propio":
            if tiene_garantia:
                return f"G/{codigo_motivo}"
            else:
                return codigo_motivo
    
    # Para Paciente/Particular
    elif quien_completa == "Paciente/Particular":
        equipo_origen = data.get('equipo_origen', '')
        
        # Si se lo entregaron
        if equipo_origen == "Se lo entregaron":
            return codigo_motivo
        
        # Si lo compró de manera directa
        elif equipo_origen == "Lo compró de manera directa":
            if tiene_garantia:
                return f"G/{codigo_motivo}"
            else:
                return codigo_motivo
    
    return "N/A"

def insertar_solicitud(data, pdf_url=None):
    """Inserta la solicitud y los equipos en la base de datos"""
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Extraer datos básicos
        email = data.get('email')
        quien_completa = data.get('quien_completa', '')
        area_solicitante = data.get('area_solicitante', '')
        
        # Extraer datos específicos según tipo de solicitante
        # Para Distribuidor e Institución
        nombre_fantasia = data.get('nombre_fantasia', None)
        razon_social = data.get('razon_social', None)
        cuit = data.get('cuit', None)
        contacto_nombre = data.get('contacto_nombre', None)
        contacto_telefono = data.get('contacto_telefono', None)
        comercial_syemed = data.get('comercial_syemed', None)
        contacto_tecnico = data.get('contacto_tecnico', None)
        
        # Para Paciente/Particular
        nombre_apellido_paciente = data.get('nombre_apellido_paciente', None)
        telefono_paciente = data.get('telefono_paciente', None)
        equipo_origen = data.get('equipo_origen', None)
        
        # Para Colaborador (ya existen en tu BD: solicitante, nivel_urgencia, equipo_corresponde_a)
        solicitante = data.get('solicitante', None)
        
        # Convertir nivel_urgencia numérico a texto
        nivel_urgencia_num = data.get('nivel_urgencia')
        if nivel_urgencia_num is not None:
            # Convertir a entero si es necesario
            nivel_urgencia_num = int(nivel_urgencia_num) if isinstance(nivel_urgencia_num, str) else nivel_urgencia_num
            
            if nivel_urgencia_num <= 1:
                nivel_urgencia = f"Bajo ({nivel_urgencia_num})"
            elif 1 < nivel_urgencia_num <= 3:
                nivel_urgencia = f"Medio ({nivel_urgencia_num})"
            else:  # 4-5
                nivel_urgencia = f"Alto ({nivel_urgencia_num})"
        else:
            nivel_urgencia = None
        
        equipo_corresponde_a = data.get('equipo_corresponde_a', None)
        equipo_propiedad = data.get('equipo_propiedad', None)
        
        # Otros campos
        motivo_solicitud = data.get('motivo_solicitud', None)
        comentarios_caso = data.get('comentarios_caso', None)
        logistica_cargo = data.get('logistica_cargo', None)
        
        # Generar código de categoría
        categoria = generar_codigo_categoria(data)
        
        # IMPORTANTE: Calcular observacion_ingreso ANTES de insertar la solicitud
        # para poder usarlo también en detalle_fallo
        observacion_ingreso = None
        
        if motivo_solicitud == "Servicio Técnico (reparaciones de equipos en general)":
            # Para ST: fallas + detalle + diagnóstico
            partes = []
            fallas = data.get('fallas_problemas', [])
            if fallas:
                partes.append(', '.join(fallas))
            detalle = data.get('detalle_fallo', '')
            if detalle:
                partes.append(detalle)
            diagnostico = data.get('diagnostico_paciente', '')
            if diagnostico:
                partes.append(f"Diagnóstico: {diagnostico}")
            
            if partes:
                observacion_ingreso = ' | '.join(partes)
        
        elif motivo_solicitud == "Servicio Post Venta (para alguno de nuestros productos adquiridos)":
            # Para Asistencia Técnica: consultas + detalle + diagnóstico
            partes = []
            fallas = data.get('fallas_problemas', [])
            if fallas:
                partes.append(', '.join(fallas))
            detalle = data.get('detalle_fallo', '')
            if detalle:
                partes.append(detalle)
            diagnostico = data.get('diagnostico_paciente', '')
            if diagnostico:
                partes.append(f"Diagnóstico: {diagnostico}")
            
            if partes:
                observacion_ingreso = ' | '.join(partes)
        
        elif motivo_solicitud == "Baja de Alquiler":
            # Para Baja de Alquiler: motivo + observación + estado
            partes = []
            motivo_baja = data.get('motivo_baja', '')
            if motivo_baja:
                partes.append(f"Motivo: {motivo_baja}")
            observacion_baja = data.get('observacion_baja', '')
            if observacion_baja:
                partes.append(observacion_baja)
            estado_equipo = data.get('estado_equipo', '')
            if estado_equipo:
                partes.append(f"Estado: {estado_equipo}")
            
            if partes:
                observacion_ingreso = ' | '.join(partes)
        
        elif motivo_solicitud == "Cambio de Alquiler":
            # Para Cambio de Alquiler: motivo del cambio
            observacion_ingreso = data.get('motivo_cambio_alquiler', '')
        
        elif motivo_solicitud == "Cambio por falla de funcionamiento crítica":
            # Para Falla Crítica: descripción + diagnóstico
            partes = []
            detalle = data.get('detalle_fallo', '')
            if detalle:
                partes.append(detalle)
            diagnostico = data.get('diagnostico_paciente', '')
            if diagnostico:
                partes.append(f"Diagnóstico: {diagnostico}")
            
            if partes:
                observacion_ingreso = ' | '.join(partes)
        
        # Usar observacion_ingreso como detalle_fallo en la tabla solicitudes
        detalle_fallo = observacion_ingreso
        
        # Insertar en la tabla solicitudes
        cursor.execute("""
            INSERT INTO solicitudes (
                fecha_solicitud, email_solicitante, quien_completa,
                area_solicitante, solicitante, nivel_urgencia,
                logistica_cargo, equipo_corresponde_a, equipo_propiedad,
                nombre_fantasia, razon_social, cuit, contacto_nombre, contacto_telefono,
                comercial_syemed, contacto_tecnico,
                nombre_apellido_paciente, telefono_paciente, equipo_origen,
                motivo_solicitud, detalle_fallo, comentarios_caso,
                categoria, estado, pdf_url
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                %s, %s, %s, %s, %s
            )
            RETURNING id
        """, (
            ahora_buenos_aires(),
            email,
            quien_completa,
            area_solicitante,
            solicitante,
            nivel_urgencia,
            logistica_cargo,
            equipo_corresponde_a,
            equipo_propiedad,
            nombre_fantasia,
            razon_social,
            cuit,
            contacto_nombre,
            contacto_telefono,
            comercial_syemed,
            contacto_tecnico,
            nombre_apellido_paciente,
            telefono_paciente,
            equipo_origen,
            motivo_solicitud,
            detalle_fallo,
            comentarios_caso,
            categoria,
            'Pendiente',
            pdf_url
        ))
        
        solicitud_id = cursor.fetchone()[0]
            
        # Determinar el nombre del cliente según el tipo de solicitante
        cliente = "Syemed"
        quien_completa = data.get('quien_completa', '')
        equipo_propiedad = data.get('equipo_propiedad', '')

        if quien_completa == "Distribuidor":
            cliente = "Syemed" if equipo_propiedad == "Alquilado" else data.get('nombre_fantasia', 'Syemed')
        elif quien_completa == "Institución":
            cliente = "Syemed" if equipo_propiedad == "Alquilado" else data.get('nombre_fantasia', 'Syemed')
        elif quien_completa == "Paciente/Particular":
            cliente = data.get('nombre_apellido_paciente', 'Syemed')
        elif quien_completa == "Colaborador de Syemed":
            equipo_corresponde_a = data.get('equipo_corresponde_a', '')
            if equipo_corresponde_a == "Distribuidor":
                cliente = "Syemed" if equipo_propiedad == "Alquilado" else data.get('nombre_fantasia', 'Syemed')
            elif equipo_corresponde_a == "Institución":
                cliente = "Syemed" if equipo_propiedad == "Alquilado" else data.get('nombre_fantasia', 'Syemed')
            elif equipo_corresponde_a == "Paciente/Particular":
                cliente = data.get('nombre_apellido_paciente', 'Syemed')
        
        # observacion_ingreso ya fue calculado arriba, no hace falta recalcularlo
        
        # Insertar equipos (CON fecha_ingreso, OST se genera automático)
        # CAMBIO: Asistencia Técnica NO genera OST ni se guarda en equipos
        equipos_ids = []
        equipos_osts = []  # Para devolver los OST generados
        
        # Verificar si la columna factura_url existe en la tabla equipos (retrocompatibilidad)
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='equipos' AND column_name='factura_url'
        """)
        factura_url_existe = cursor.fetchone() is not None
        
        # Solo insertar equipos si NO es Asistencia Técnica
        if motivo_solicitud != "Servicio Post Venta (para alguno de nuestros productos adquiridos)":
            for i, equipo in enumerate(data.get('equipos', []), 1):
                if equipo.get('tipo_equipo') and equipo['tipo_equipo'] != "Seleccionar tipo...":
                    
                    if factura_url_existe:
                        # VERSIÓN CON factura_url (BD actualizada)
                        cursor.execute("""
                            INSERT INTO equipos (
                                solicitud_id, numero_equipo, tipo_equipo, marca, modelo,
                                numero_serie, en_garantia, fecha_compra, factura_url, cliente,
                                remito, accesorios, prioridad, observacion_ingreso,
                                fecha_ingreso
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id, ost
                        """, (
                            solicitud_id, 
                            i,
                            equipo['tipo_equipo'],
                            equipo.get('marca'),
                            equipo.get('modelo'),
                            equipo.get('numero_serie'),
                            equipo.get('en_garantia'),
                            equipo.get('fecha_compra'),
                            equipo.get('factura_url'),  # URL de la factura
                            cliente,
                            None,  # remito
                            None,  # accesorios
                            None,  # prioridad
                            observacion_ingreso,
                            ahora_buenos_aires()  # fecha_ingreso
                        ))
                    else:
                        # VERSIÓN SIN factura_url (retrocompatible)
                        cursor.execute("""
                            INSERT INTO equipos (
                                solicitud_id, numero_equipo, tipo_equipo, marca, modelo,
                                numero_serie, en_garantia, fecha_compra, cliente,
                                remito, accesorios, prioridad, observacion_ingreso,
                                fecha_ingreso
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id, ost
                        """, (
                            solicitud_id, 
                            i,
                            equipo['tipo_equipo'],
                            equipo.get('marca'),
                            equipo.get('modelo'),
                            equipo.get('numero_serie'),
                            equipo.get('en_garantia'),
                            equipo.get('fecha_compra'),
                            cliente,
                            None,  # remito
                            None,  # accesorios
                            None,  # prioridad
                            observacion_ingreso,
                            ahora_buenos_aires()  # fecha_ingreso
                        ))
                    
                    resultado = cursor.fetchone()
                    equipo_id = resultado[0]
                    ost = resultado[1]
                    
                    equipos_ids.append(equipo_id)
                    equipos_osts.append(ost)
        
        # Insertar archivos adjuntos en la tabla archivos_adjuntos
        if 'archivos_urls' in data and data['archivos_urls']:
            for archivo_info in data['archivos_urls']:
                tipo_archivo = archivo_info.get('tipo')
                
                # Determinar categoría y equipo_id
                categoria = 'general'
                equipo_id_ref = None
                
                if tipo_archivo == 'factura':
                    categoria = 'factura'
                    # Vincular factura al equipo correspondiente
                    equipo_num = archivo_info.get('equipo_num')
                    
                    # Si equipo_num es 'todos', vincular al primer equipo
                    # La factura también se guarda en equipos.factura_url para todos
                    if equipo_num == 'todos' and equipos_ids:
                        equipo_id_ref = equipos_ids[0]  # Vincular al primer equipo
                    elif isinstance(equipo_num, int) and equipo_num <= len(equipos_ids):
                        equipo_id_ref = equipos_ids[equipo_num - 1]
                
                elif tipo_archivo == 'foto_video':
                    categoria = 'falla'
                    # Vincular foto al equipo correspondiente
                    equipo_num = archivo_info.get('equipo_num', 1)
                    if equipo_num <= len(equipos_ids):
                        equipo_id_ref = equipos_ids[equipo_num - 1]
                    else:
                        equipo_id_ref = None  # Fallback
                
                # Insertar en archivos_adjuntos
                cursor.execute("""
                    INSERT INTO archivos_adjuntos (
                        solicitud_id, equipo_id, nombre_archivo, url_cloudinary,
                        tipo_archivo, tamano_bytes, fecha_subida, categoria
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    solicitud_id,
                    equipo_id_ref,
                    archivo_info.get('nombre'),
                    archivo_info.get('url'),
                    archivo_info.get('nombre', '').split('.')[-1].lower(),
                    archivo_info.get('tamano'),
                    ahora_buenos_aires(),
                    categoria
                ))
        
        conn.commit()
        
        # Mostrar información de OSTs generados en la consola (para debug)
        if equipos_osts:
            print(f"\n✅ OSTs generados: {', '.join(map(str, equipos_osts))}")
        
        return True, solicitud_id, equipos_osts
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error en insertar_solicitud: {str(e)}")  # Debug
        return False, str(e), []
    finally:
        if conn:
            conn.close()
# ============================================================================
# 1. RATE LIMITING - Limitar solicitudes por IP/Email
# ============================================================================

def obtener_rate_limit_key():
    """Obtiene un identificador único del usuario (IP o session)"""
    # Streamlit no expone la IP directamente, usamos session_id
    if 'user_id' not in st.session_state:
        st.session_state.user_id = hashlib.md5(str(time.time()).encode()).hexdigest()
    return st.session_state.user_id

def verificar_rate_limit(max_solicitudes=3, ventana_minutos=60):
    """
    Limita el número de solicitudes por usuario
    
    Args:
        max_solicitudes: Máximo de solicitudes permitidas
        ventana_minutos: Ventana de tiempo en minutos
    
    Returns:
        tuple: (permitido: bool, mensaje: str, tiempo_restante: int)
    """
    if 'rate_limit' not in st.session_state:
        st.session_state.rate_limit = {}
    
    user_key = obtener_rate_limit_key()
    ahora = ahora_buenos_aires()
    
    # Limpiar registros antiguos
    if user_key in st.session_state.rate_limit:
        st.session_state.rate_limit[user_key] = [
            timestamp for timestamp in st.session_state.rate_limit[user_key]
            if ahora - timestamp < timedelta(minutes=ventana_minutos)
        ]
    
    # Verificar límite
    solicitudes_recientes = len(st.session_state.rate_limit.get(user_key, []))
    
    if solicitudes_recientes >= max_solicitudes:
        tiempo_mas_antiguo = min(st.session_state.rate_limit[user_key])
        tiempo_restante = int((tiempo_mas_antiguo + timedelta(minutes=ventana_minutos) - ahora).total_seconds() / 60)
        return False, f"Has alcanzado el límite de {max_solicitudes} solicitudes por hora. Intenta en {tiempo_restante} minutos.", tiempo_restante
    
    return True, "OK", 0

def registrar_solicitud_rate_limit():
    """Registra una nueva solicitud para el rate limiting"""
    user_key = obtener_rate_limit_key()
    if user_key not in st.session_state.rate_limit:
        st.session_state.rate_limit[user_key] = []
    st.session_state.rate_limit[user_key].append(ahora_buenos_aires())


# ============================================================================
# 2. VALIDACIÓN DE ARCHIVOS - Prevenir malware y archivos peligrosos
# ============================================================================

# Extensiones permitidas
EXTENSIONES_PERMITIDAS = {
    'imagenes': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'],
    'videos': ['.mp4', '.mov', '.avi', '.mkv', '.webm'],
    'documentos': ['.pdf', '.doc', '.docx', '.txt']
}

# MIME types permitidos
MIME_TYPES_PERMITIDOS = {
    'image/jpeg', 'image/png', 'image/gif', 'image/bmp', 'image/webp',
    'video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska', 'video/webm',
    'application/pdf', 'application/msword', 
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain'
}

# Tamaños máximos (en MB)
TAMANO_MAX_IMAGEN = 10  # 10 MB
TAMANO_MAX_VIDEO = 50   # 50 MB
TAMANO_MAX_DOCUMENTO = 5 # 5 MB

def validar_extension_archivo(nombre_archivo):
    """Valida que la extensión del archivo sea permitida"""
    extension = '.' + nombre_archivo.lower().split('.')[-1]
    todas_extensiones = [ext for lista in EXTENSIONES_PERMITIDAS.values() for ext in lista]
    return extension in todas_extensiones

def validar_mime_type(archivo):
    """Valida el MIME type real del archivo (no solo la extensión)"""
    try:
        # Leer los primeros bytes para detectar el tipo real
        archivo.seek(0)
        mime = magic.from_buffer(archivo.read(2048), mime=True)
        archivo.seek(0)
        return mime in MIME_TYPES_PERMITIDOS, mime
    except Exception as e:
        st.warning(f"No se pudo verificar el tipo de archivo: {e}")
        return False, "unknown"

def validar_tamano_archivo(archivo):
    """Valida el tamaño del archivo según su tipo"""
    tamano_mb = archivo.size / (1024 * 1024)
    extension = '.' + archivo.name.lower().split('.')[-1]
    
    if extension in EXTENSIONES_PERMITIDAS['imagenes']:
        if tamano_mb > TAMANO_MAX_IMAGEN:
            return False, f"La imagen supera el tamaño máximo de {TAMANO_MAX_IMAGEN}MB"
    elif extension in EXTENSIONES_PERMITIDAS['videos']:
        if tamano_mb > TAMANO_MAX_VIDEO:
            return False, f"El video supera el tamaño máximo de {TAMANO_MAX_VIDEO}MB"
    elif extension in EXTENSIONES_PERMITIDAS['documentos']:
        if tamano_mb > TAMANO_MAX_DOCUMENTO:
            return False, f"El documento supera el tamaño máximo de {TAMANO_MAX_DOCUMENTO}MB"
    
    return True, f"{tamano_mb:.2f}MB"

def escanear_nombre_archivo(nombre_archivo):
    """Detecta nombres de archivo sospechosos"""
    patrones_sospechosos = [
        r'\.exe$', r'\.bat$', r'\.cmd$', r'\.sh$', r'\.ps1$',
        r'\.scr$', r'\.vbs$', r'\.js$', r'\.jar$', r'\.com$',
        r'\.pif$', r'\.msi$', r'\.dll$', r'\.sys$'
    ]
    
    for patron in patrones_sospechosos:
        if re.search(patron, nombre_archivo.lower()):
            return False, f"Extensión no permitida: {patron}"
    
    # Detectar doble extensión (ej: documento.pdf.exe)
    partes = nombre_archivo.split('.')
    if len(partes) > 2:
        return False, "Archivo con múltiples extensiones no permitido"
    
    return True, "OK"

def validar_archivo_completo(archivo):
    """
    Validación completa de un archivo
    
    Returns:
        tuple: (es_valido: bool, mensaje: str)
    """
    # 1. Validar nombre
    valido_nombre, msg_nombre = escanear_nombre_archivo(archivo.name)
    if not valido_nombre:
        return False, f"❌ Nombre inválido: {msg_nombre}"
    
    # 2. Validar extensión
    if not validar_extension_archivo(archivo.name):
        return False, f"❌ Extensión no permitida: {archivo.name}"
    
    # 3. Validar tamaño
    valido_tamano, msg_tamano = validar_tamano_archivo(archivo)
    if not valido_tamano:
        return False, f"❌ {msg_tamano}"
    
    # 4. Validar MIME type (requiere python-magic)
    try:
        valido_mime, mime_type = validar_mime_type(archivo)
        if not valido_mime:
            return False, f"❌ Tipo de archivo no permitido: {mime_type}"
    except:
        # Si python-magic no está instalado, continuar sin esta validación
        st.warning("⚠️ Validación de tipo de archivo no disponible. Instala python-magic para mayor seguridad.")
    
    return True, f"✅ Archivo válido ({msg_tamano})"


# ============================================================================
# 3. SANITIZACIÓN DE INPUTS - Prevenir SQL Injection y XSS
# ============================================================================

def sanitizar_texto(texto, max_length=500):
    """Sanitiza texto para prevenir inyecciones"""
    if not texto:
        return ""
    
    # Limitar longitud
    texto = str(texto)[:max_length]
    
    # Eliminar caracteres peligrosos
    caracteres_peligrosos = ['<', '>', '{', '}', '|', '\\', '^', '~', '[', ']', '`']
    for char in caracteres_peligrosos:
        texto = texto.replace(char, '')
    
    # Eliminar múltiples espacios
    texto = ' '.join(texto.split())
    
    return texto.strip()

def sanitizar_email(email):
    """Validación estricta de email"""
    # Patrón más restrictivo
    patron = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(patron, email):
        return None
    return email.lower().strip()

def sanitizar_numero_serie(numero_serie):
    """Sanitiza número de serie permitiendo solo alfanuméricos y guiones"""
    if not numero_serie:
        return ""
    # Solo letras, números, guiones y espacios
    return re.sub(r'[^a-zA-Z0-9\-\s]', '', str(numero_serie)).strip()


# ============================================================================
# 4. CAPTCHA SIMPLE - Prevenir bots
# ============================================================================

def generar_captcha():
    """Genera un captcha matemático simple"""
    import random
    num1 = random.randint(1, 5)
    num2 = random.randint(1, 5)
    respuesta_correcta = num1 + num2
    
    if 'captcha_respuesta' not in st.session_state:
        st.session_state.captcha_respuesta = respuesta_correcta
        st.session_state.captcha_pregunta = f"¿Cuánto es {num1} + {num2}?"
    
    return st.session_state.captcha_pregunta, st.session_state.captcha_respuesta

def verificar_captcha(respuesta_usuario):
    """Verifica la respuesta del captcha"""
    try:
        return int(respuesta_usuario) == st.session_state.captcha_respuesta
    except:
        return False

def mostrar_captcha():
    """Muestra el captcha en el formulario"""
    pregunta, respuesta_correcta = generar_captcha()
    
    st.markdown("---")
    st.markdown("### 🤖 Verificación de seguridad")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        respuesta_usuario = st.text_input(
            pregunta,
            key="captcha_input",
            help="Por favor resuelve esta operación matemática para continuar"
        )
    
    if respuesta_usuario:
        if verificar_captcha(respuesta_usuario):
            st.success("✅ Verificación correcta")
            return True
        else:
            st.error("❌ Respuesta incorrecta. Intenta nuevamente.")
            return False
    
    return False


# ============================================================================
# 5. HONEYPOT - Trampa para bots
# ============================================================================

def agregar_honeypot():
    """
    Agrega un campo oculto que solo los bots llenarán
    Los usuarios reales no lo verán debido al CSS
    """
    st.markdown("""
    <style>
    .honeypot {
        position: absolute;
        left: -9999px;
        width: 1px;
        height: 1px;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Campo honeypot (oculto con CSS)
    honeypot_value = st.text_input(
        "Si eres humano, deja este campo vacío",
        key="honeypot_field",
        label_visibility="collapsed"
    )
    
    return honeypot_value

def verificar_honeypot(honeypot_value):
    """Verifica que el honeypot esté vacío"""
    return not honeypot_value or honeypot_value.strip() == ""


# ============================================================================
# 6. LOGGING DE SEGURIDAD - Registrar intentos sospechosos
# ============================================================================

import json
from pathlib import Path

def log_evento_seguridad(tipo_evento, detalles):
    """Registra eventos de seguridad en un archivo log"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / f"seguridad_{ahora_buenos_aires().strftime('%Y%m')}.log"
    
    evento = {
        'timestamp': ahora_buenos_aires().isoformat(),
        'tipo': tipo_evento,
        'user_id': obtener_rate_limit_key(),
        'detalles': detalles
    }
    
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps(evento, ensure_ascii=False) + '\n')

def registrar_intento_sospechoso(razon, datos_adicionales=None):
    """Registra un intento sospechoso"""
    detalles = {
        'razon': razon,
        'datos': datos_adicionales or {}
    }
    log_evento_seguridad('INTENTO_SOSPECHOSO', detalles)
    
    # Incrementar contador de intentos sospechosos
    if 'intentos_sospechosos' not in st.session_state:
        st.session_state.intentos_sospechosos = 0
    st.session_state.intentos_sospechosos += 1
    
    # Bloquear después de 3 intentos sospechosos
    if st.session_state.intentos_sospechosos >= 3:
        st.error("🚫 Has sido bloqueado temporalmente por actividad sospechosa.")
        st.stop()


# ============================================================================
# 7. INTEGRACIÓN CON EL FORMULARIO
# ============================================================================

def aplicar_seguridad_formulario(data, archivos_fotos=None, archivos_facturas=None):
    """
    Aplica todas las validaciones de seguridad al formulario
    
    Returns:
        tuple: (aprobado: bool, mensaje: str)
    """
    
    # 1. Verificar Rate Limit
    permitido, msg_rate, tiempo = verificar_rate_limit(max_solicitudes=5, ventana_minutos=60)
    if not permitido:
        registrar_intento_sospechoso('RATE_LIMIT_EXCEDIDO', {'tiempo_restante': tiempo})
        return False, f"⏱️ {msg_rate}"
    
    # 2. Verificar Honeypot
    honeypot = agregar_honeypot()
    if not verificar_honeypot(honeypot):
        registrar_intento_sospechoso('HONEYPOT_LLENO', {'valor': honeypot})
        return False, "❌ Validación de seguridad fallida."
    
    # 3. Validar archivos (fotos por equipo)
    for equipo in data.get('equipos', []):
        if 'fotos_fallas' in equipo and equipo['fotos_fallas']:
            for archivo in equipo['fotos_fallas']:
                valido, mensaje = validar_archivo_completo(archivo)
                if not valido:
                    registrar_intento_sospechoso('ARCHIVO_INVALIDO', {'archivo': archivo.name, 'razon': mensaje})
                    return False, f"📁 {mensaje}"
    
    if archivos_facturas:
        for archivo in archivos_facturas:
            if archivo:  # Puede ser None
                valido, mensaje = validar_archivo_completo(archivo)
                if not valido:
                    registrar_intento_sospechoso('ARCHIVO_INVALIDO', {'archivo': archivo.name, 'razon': mensaje})
                    return False, f"📁 {mensaje}"
    
    # 4. Sanitizar textos
    data['email'] = sanitizar_email(data.get('email', ''))
    if not data['email']:
        return False, "❌ Email inválido"
    
    campos_texto = ['comentarios_caso', 'detalle_fallo', 'diagnostico_paciente', 
                    'nombre_fantasia', 'razon_social', 'contacto_nombre']
    for campo in campos_texto:
        if campo in data and data[campo]:
            data[campo] = sanitizar_texto(data[campo], max_length=1000)
    
    # Sanitizar números de serie
    for equipo in data.get('equipos', []):
        if 'numero_serie' in equipo:
            equipo['numero_serie'] = sanitizar_numero_serie(equipo['numero_serie'])
    
    # 5. Registrar solicitud exitosa
    registrar_solicitud_rate_limit()
    log_evento_seguridad('SOLICITUD_EXITOSA', {
        'email': data.get('email'),
        'tipo_solicitante': data.get('quien_completa'),
        'num_equipos': len(data.get('equipos', []))
    })
    
    return True, "✅ Validaciones de seguridad aprobadas"
def mostrar_flujo_motivo_solicitud_distribuidor_institucion(data, tipo_cliente, form_key):
    """
    Flujo condicional para Distribuidor e Institución
    
    Pregunta inicial: ¿El equipo es alquilado o propio?
    - Si Alquilado -> mostrar: ST, Asistencia Técnica, Baja Alquiler, Cambio Alquiler, Cambio por falla crítica
    - Si Propio -> Pregunta: ¿Nos lo compró de manera directa?
        - Si Sí -> ¿Está en garantía? (Sí, No, No lo sé)
            - Si Sí -> Permitir cargar factura + mostrar: ST, Asistencia Técnica, Cambio por falla crítica
            - Si No o No lo sé -> mostrar: ST, Asistencia Técnica, Cambio por falla crítica
        - Si No -> mostrar: ST, Asistencia Técnica, Cambio por falla crítica
    """
    
    st.markdown('<div class="section-header"><h3>Información del Equipo</h3></div>', unsafe_allow_html=True)
    
    # PREGUNTA INICIAL: ¿El equipo es alquilado o propio?
    equipo_propiedad = st.selectbox(
        "¿El equipo es alquilado o propio? *",
        ["", "Alquilado", "Propio"],
        key=f"{tipo_cliente}_propiedad_{form_key}"
    )
    
    # Variables para almacenar
    motivo_solicitud = ""
    compra_directa = None
    en_garantia = None
    fecha_compra = None
    factura_garantia = None
    motivo_cambio_alquiler = ""
    
    # FLUJO PARA ALQUILADO
    if equipo_propiedad == "Alquilado":
        motivo_solicitud = st.selectbox(
            "Motivo de la solicitud *",
            ["",
             "Servicio Técnico (reparaciones de equipos en general)",
             "Asistencia Técnica",
             "Baja de Alquiler",
             "Cambio de Alquiler",
             "Cambio por falla crítica"],
            key=f"{tipo_cliente}_motivo_alquilado_{form_key}"
        )
        
        # Si es Cambio de Alquiler, pedir motivo
        if motivo_solicitud == "Cambio de Alquiler":
            st.info("📝 Por favor, especifique el motivo del cambio de alquiler")
            motivo_cambio_alquiler = st.text_area(
                "Motivo del cambio de alquiler *",
                placeholder="Ej: Cambio de equipo por uno de mayor capacidad, equipo obsoleto, etc.",
                key=f"{tipo_cliente}_motivo_cambio_{form_key}",
                height=100
            )
    
    # FLUJO PARA PROPIO
    elif equipo_propiedad == "Propio":
        # Pregunta: ¿Nos lo compró de manera directa?
        compra_directa = st.selectbox(
            "¿El equipo nos lo compró de manera directa? *",
            ["", "Sí", "No"],
            key=f"{tipo_cliente}_compra_directa_{form_key}"
        )
        
        # SI COMPRÓ DIRECTA
        if compra_directa == "Sí":
            en_garantia = st.selectbox(
                "¿Está en garantía? *",
                ["", "Sí", "No", "No lo sé"],
                key=f"{tipo_cliente}_garantia_{form_key}"
            )
            
            # Si está en garantía, permitir cargar factura
            if en_garantia == "Sí":
                col1, col2 = st.columns(2)
                with col1:
                    fecha_compra = st.date_input(
                        "Fecha de Compra *",
                        value=None,
                        max_value=date.today(),
                        format="DD/MM/YYYY",
                        key=f"{tipo_cliente}_fecha_compra_{form_key}",
                        help="No puede seleccionar fechas futuras"
                    )
                with col2:
                    factura_garantia = st.file_uploader(
                        "Adjunte factura *",
                        type=['pdf', 'jpg', 'jpeg', 'png'],
                        key=f"{tipo_cliente}_factura_{form_key}"
                    )
                
                # Mostrar motivos disponibles
                motivo_solicitud = st.selectbox(
                    "Motivo de la solicitud *",
                    ["",
                     "Servicio Técnico (reparaciones de equipos en general)",
                     "Asistencia Técnica",
                     "Cambio por falla crítica"],
                    key=f"{tipo_cliente}_motivo_garantia_{form_key}"
                )
            
            # Si NO está en garantía o No lo sé
            elif en_garantia in ["No", "No lo sé"]:
                motivo_solicitud = st.selectbox(
                    "Motivo de la solicitud *",
                    ["",
                     "Servicio Técnico (reparaciones de equipos en general)",
                     "Asistencia Técnica",
                     "Cambio por falla crítica"],
                    key=f"{tipo_cliente}_motivo_sin_garantia_{form_key}"
                )
        
        # SI NO COMPRÓ DIRECTA
        elif compra_directa == "No":
            motivo_solicitud = st.selectbox(
                "Motivo de la solicitud *",
                ["",
                 "Servicio Técnico (reparaciones de equipos en general)",
                 "Asistencia Técnica",
                 "Cambio por falla crítica"],
                key=f"{tipo_cliente}_motivo_no_directo_{form_key}"
            )
    
    # Normalizar motivo
    if motivo_solicitud:
        motivo_solicitud = normalizar_motivo_solicitud(motivo_solicitud)
    
    # Retornar todos los datos
    return {
        'equipo_propiedad': equipo_propiedad,
        'compra_directa': compra_directa,
        'en_garantia': en_garantia,
        'fecha_compra': fecha_compra,
        'factura_garantia': factura_garantia,
        'motivo_solicitud': motivo_solicitud,
        'motivo_cambio_alquiler': motivo_cambio_alquiler
    }


def mostrar_flujo_motivo_solicitud_paciente(data, form_key):
    """
    Flujo condicional para Paciente/Particular
    
    Pregunta inicial: El equipo...
    - Se lo entregaron? -> ¿Quién lo entregó? -> Fecha -> Habilitar motivo: ST, Asistencia Técnica, Cambio por falla crítica
    - Lo compró de manera directa? -> ¿Está en garantía? (Sí, No, No lo sé)
        - Si Sí -> Cargar factura -> Habilitar motivo: ST, Asistencia Técnica, Cambio por falla crítica
        - Si No o No lo sé -> Habilitar motivo: ST, Asistencia Técnica, Cambio por falla crítica
    """
    
    st.markdown('<div class="section-header"><h3>Información del Equipo</h3></div>', unsafe_allow_html=True)
    
    # PREGUNTA INICIAL
    equipo_origen = st.selectbox(
        "El equipo... *",
        ["", "Se lo entregaron", "Lo compró de manera directa"],
        key=f"p_origen_{form_key}"
    )
    
    # Variables
    quien_entrego = ""
    fecha_entrega = None
    en_garantia = None
    fecha_compra = None
    factura_garantia = None
    motivo_solicitud = ""
    
    # FLUJO: SE LO ENTREGARON
    if equipo_origen == "Se lo entregaron":
        quien_entrego = st.text_area(
            "¿Quién lo entregó? *",
            placeholder="Obra Social, Distribuidor, Ortopedia, Plataformas Digitales, etc.",
            key=f"p_quien_entrego_{form_key}",
            height=80
        )
        
        fecha_entrega = st.date_input(
            "Fecha de entrega *",
            value=None,
            max_value=date.today(),
            format="DD/MM/YYYY",
            key=f"p_fecha_entrega_{form_key}",
            help="Fecha aproximada en que recibió el equipo"
        )
        
        # Habilitar motivo
        motivo_solicitud = st.selectbox(
            "Motivo de la solicitud *",
            ["",
             "Servicio Técnico (reparaciones de equipos en general)",
             "Asistencia Técnica",
             "Cambio por falla crítica"],
            key=f"p_motivo_entregado_{form_key}"
        )
    
    # FLUJO: LO COMPRÓ DE MANERA DIRECTA
    elif equipo_origen == "Lo compró de manera directa":
        en_garantia = st.selectbox(
            "¿Está en garantía? *",
            ["", "Sí", "No", "No lo sé"],
            key=f"p_garantia_{form_key}"
        )
        
        # Si está en garantía, cargar factura
        if en_garantia == "Sí":
            col1, col2 = st.columns(2)
            with col1:
                fecha_compra = st.date_input(
                    "Fecha de Compra *",
                    value=None,
                    max_value=date.today(),
                    format="DD/MM/YYYY",
                    key=f"p_fecha_compra_{form_key}",
                    help="No puede seleccionar fechas futuras"
                )
            with col2:
                factura_garantia = st.file_uploader(
                    "Adjunte factura *",
                    type=['pdf', 'jpg', 'jpeg', 'png'],
                    key=f"p_factura_{form_key}"
                )
            
            motivo_solicitud = st.selectbox(
                "Motivo de la solicitud *",
                ["",
                 "Servicio Técnico (reparaciones de equipos en general)",
                 "Asistencia Técnica",
                 "Cambio por falla crítica"],
                key=f"p_motivo_garantia_{form_key}"
            )
        
        # Si NO está en garantía o No lo sé
        elif en_garantia in ["No", "No lo sé"]:
            motivo_solicitud = st.selectbox(
                "Motivo de la solicitud *",
                ["",
                 "Servicio Técnico (reparaciones de equipos en general)",
                 "Asistencia Técnica",
                 "Cambio por falla crítica"],
                key=f"p_motivo_sin_garantia_{form_key}"
            )
    
    # Normalizar motivo
    if motivo_solicitud:
        motivo_solicitud = normalizar_motivo_solicitud(motivo_solicitud)
    
    return {
        'equipo_origen': equipo_origen,
        'quien_entrego': quien_entrego,
        'fecha_entrega': fecha_entrega,
        'en_garantia': en_garantia,
        'fecha_compra': fecha_compra,
        'factura_garantia': factura_garantia,
        'motivo_solicitud': motivo_solicitud
    }


def normalizar_motivo_solicitud(motivo_texto):
    """
    Normaliza el texto del motivo para compatibilidad con la BD
    
    NUEVA VERSIÓN - Reemplazar la función existente
    """
    if not motivo_texto:
        return ""
    
    # Mapeo de textos cortos a valores largos en BD
    mapeo = {
        "Asistencia Técnica": "Servicio Post Venta (para alguno de nuestros productos adquiridos)",
        "Cambio por falla crítica": "Cambio por falla de funcionamiento crítica"
    }
    
    return mapeo.get(motivo_texto, motivo_texto)


def main():
    # Inicializar form_key si no existe
    if 'form_key' not in st.session_state:
        st.session_state.form_key = 0
    
    # Si el formulario fue enviado, mostrar solo el resumen
    if st.session_state.get('formulario_enviado', False):
        mostrar_resumen_y_descarga()
        return
    
    # Header principal
    st.markdown("""
    <style>
    .main-header {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .header-content {
        display: flex;
        align-items: center;
        gap: 20px;
        flex-wrap: wrap;
        margin-bottom: 15px;
    }
    .header-content img {
        max-width: 200px;
        height: auto;
    }
    .titulo-completo { display: block; }
    .titulo-corto { display: none; }

    @media (max-width: 768px) {
        .titulo-completo { display: none; }
        .titulo-corto { display: block; }
        .header-content {
            justify-content: center;
            text-align: center;
        }
        .header-content img {
            max-width: 80px;
        }
    }
    </style>
    <div class="main-header">
        <div class="header-content">
            <div>
                <h1 class="titulo-completo">Post Venta y ST: Solicitud de Servicio</h1>
                <h1 class="titulo-corto">Solicitud de Servicio</h1>
            </div>
                <img src="https://res.cloudinary.com/dfxjqvan0/image/upload/v1762453830/LOGO_SYEMED_MUX-removebg_df3gwy.png" alt="Logo Syemed" />
        </div>
        <p><strong>Contacto:</strong> Servicio de Post Venta y ST</p>
        <p><strong>Atención:</strong> Lunes a Viernes de 8 a 17hs</p>
        <p><strong>Teléfono para urgencias:</strong> 11 2373-0278</p>
    </div>
    """, unsafe_allow_html=True)

        
    # SECCIÓN 1: Información básica
    st.markdown('<div class="section-header"><h2>Información Básica</h2></div>', unsafe_allow_html=True)
    
    email = st.text_input(
        "Correo electrónico *", 
        placeholder="ejemplo@empresa.com",
        key=f"email_{st.session_state.form_key}"
    )
    
    # VALIDACIÓN DEL EMAIL
    email_valido = False
    email_normalizado = ""
    
    if email:
        es_valido, mensaje, email_normalizado = validar_email_formato(email)
        
        if es_valido:
            st.success(f"✓ {mensaje}")
            email_valido = True
        else:
            st.error(f"✗ {mensaje}")
            st.info("💡 Formato correcto: usuario@dominio.com")

    quien_completa = st.selectbox(
    "¿Quién está completando la solicitud? *",
    ["", "Colaborador de Syemed", "Distribuidor", "Institución", "Paciente/Particular"],
    key=f"quien_completa_{st.session_state.form_key}"
    )
    
    
    
    # Variables para almacenar datos
    data = {
        'email': email_normalizado if email_valido else email,
        'quien_completa': quien_completa
    }
    
    # Solo continuar si el email es válido Y se ha completado quien_completa
    if email_valido and quien_completa:
        
        # SECCIÓN 2: Colaboradores de Syemed
        if quien_completa == "Colaborador de Syemed":
            st.markdown('<div class="section-header"><h2>Colaboradores de Syemed</h2></div>', unsafe_allow_html=True)
            
            col1, col2 = st.columns(2)
            with col1:
                area_solicitante = st.selectbox(
                    "Área Solicitante *", 
                    ["", "Comercial", "Comex", "Logística/Depósito"],
                    key=f"area_{st.session_state.form_key}"
                )
                
            
            with col2:
                solicitante = st.selectbox(
                    "Solicitante *", 
                    SOLICITANTES_INTERNOS,
                    key=f"solicitante_{st.session_state.form_key}"
                )

            nivel_urgencia = st.slider(
                "Nivel de Urgencia *", 
                0, 5, 0, 
                help="0: No hay urgencia, 5: Extremadamente Urgente",
                key=f"urgencia_{st.session_state.form_key}"
            )

            st.markdown("**Logística a cargo de:**")
            st.info("💡 Seleccione todas las opciones que apliquen y luego continúe más abajo")
            logistica_cargo = st.multiselect(
                "Seleccione las opciones que apliquen:", 
                ["Ida a cargo de Cliente", "Ida a cargo de Syemed", "Vuelta a cargo de Cliente", "Vuelta a cargo de Syemed"],
                key=f"logistica_{st.session_state.form_key}",
                help="Puede seleccionar múltiples opciones. Haga clic fuera del menú cuando termine.",
                label_visibility="collapsed"
            )
            # Mostrar resumen de selección
            if logistica_cargo:
                st.success(f"✅ {len(logistica_cargo)} opción(es) seleccionada(s)")
                for opcion in logistica_cargo:
                    st.caption(f"  • {opcion}")
            
            comentarios_caso = st.text_area(
                "Comentarios sobre el caso", 
                placeholder="NOTA 1: En el caso de que la solicitud sea por un Equipo del Stock indicar si vuelve a stock de venta...\nNOTA 2: Colocar dirección y datos para la entrega si corresponde.", 
                height=100,
                key=f"comentarios_{st.session_state.form_key}"
            )
            
            equipo_corresponde_a = st.selectbox(
                "El equipo corresponde a: *", 
                ["", "Paciente/Particular", "Distribuidor", "Institución", "Equipo de Stock", "Baja de demo"],
                key=f"equipo_corresponde_{st.session_state.form_key}"
            )
            
            data.update({
                'area_solicitante': area_solicitante,
                'solicitante': solicitante,
                'nivel_urgencia': nivel_urgencia,
                'logistica_cargo': ', '.join(logistica_cargo),
                'comentarios_caso': comentarios_caso,
                'equipo_corresponde_a': equipo_corresponde_a
            })
            
            # Lógica condicional para Colaboradores según "El equipo corresponde a"
            if equipo_corresponde_a == "Distribuidor":
                mostrar_seccion_distribuidorB(data)
            elif equipo_corresponde_a == "Institución":
                mostrar_seccion_institucionB(data)
            elif equipo_corresponde_a == "Paciente/Particular":
                mostrar_seccion_paciente(data)
            elif equipo_corresponde_a == "Equipo de Stock":
                # Para Equipo de Stock, establecer un motivo por defecto
                data['motivo_solicitud'] = "Equipo de Stock"
                mostrar_seccion_equipos(data, contexto="stock")
            elif equipo_corresponde_a == "Baja de demo":
                data['motivo_solicitud'] = "Baja de demo"
                mostrar_seccion_equipos(data, contexto="baja_demo")

        
        # SECCIÓN 3: Distribuidor directo
        elif quien_completa == "Distribuidor":
            mostrar_seccion_distribuidor(data, es_directo=True)
        
        # SECCIÓN 4: Institución directo
        elif quien_completa == "Institución":
            mostrar_seccion_institucion(data, es_directo=True)
        
        # SECCIÓN 5: Paciente/Particular directo
        elif quien_completa == "Paciente/Particular":
            mostrar_seccion_paciente(data, es_directo=True)
        
        # Obtener el motivo según el tipo de solicitante
        motivo = data.get('motivo_solicitud', '')
        
        # SECCIÓN 6: Detalles de Servicio Técnico
        # SECCIÓN DE FALLAS/PROBLEMAS CLASIFICADA POR MOTIVO
        if motivo in ["Servicio Técnico (reparaciones de equipos en general)", 
                      "Servicio Post Venta (para alguno de nuestros productos adquiridos)", 
                      "Cambio por falla de funcionamiento crítica"]:
            st.markdown('<div class="section-header"><h2>Detalles del Servicio Técnico</h2></div>', unsafe_allow_html=True)
            
            # Inicializar variables
            fallas_seleccionadas = []
            detalle_fallo = ""
            
            # SERVICIO TÉCNICO
            if motivo == "Servicio Técnico (reparaciones de equipos en general)":
                st.markdown("#### Fallas de Servicio Técnico")
                st.info("""
                **¿Cuándo solicitar Servicio Técnico?**
                - Equipo no enciende o presenta fallas eléctricas
                - Problemas mecánicos o de funcionamiento
                - Ruidos anormales o vibraciones
                - Pérdida de precisión o calibración
                - Desgaste de componentes
                - Mantenimiento preventivo programado
                """)
                
                FALLAS_ST = [
                    "No enciende",
                    "Falla eléctrica",
                    "Problema mecánico",
                    "Ruidos anormales",
                    "Pérdida de precisión",
                    "Necesita calibración",
                    "Desgaste de piezas",
                    "Mantenimiento preventivo",
                    "Falla en display/pantalla",
                    "Problema de conectividad"
                ]
                
                fallas_seleccionadas = st.multiselect(
                    "Seleccione las fallas detectadas",
                    FALLAS_ST,
                    key=f"fallas_st_{st.session_state.form_key}"
                )
                
                # Mostrar resumen de selección
                if fallas_seleccionadas:
                    st.success(f"✅ {len(fallas_seleccionadas)} falla(s) seleccionada(s)")
                
                detalle_fallo = st.text_area(
                    "Otros problemas o detalles adicionales",
                    placeholder="Describa cualquier otro problema o detalle relevante...",
                    key=f"detalle_st_{st.session_state.form_key}",
                    height=100
                )
            
            # ASISTENCIA TÉCNICA
            elif motivo == "Servicio Post Venta (para alguno de nuestros productos adquiridos)":
                st.markdown("#### Consultas de Asistencia Técnica")
                st.info("""
                **¿Cuándo solicitar Asistencia Técnica?**
                - Dudas sobre el uso del equipo o configuración inicial.
                - Solicitud de capacitación
                - Consulta sobre garantía
                - Solicitud de manuales o documentación
                - Accesorios o repuestos
                - Actualización de software
                """)
                
                CONSULTAS_PV = [
                    "Consulta sobre uso del equipo",
                    "Solicitud de capacitación",
                    "Consulta sobre garantía",
                    "Solicitud de manual/documentación",
                    "Necesito accesorios",
                    "Necesito repuestos",
                    "Actualización de software",
                    "Configuración inicial"
                ]
                
                fallas_seleccionadas = st.multiselect(
                    "Seleccione el tipo de consulta",
                    CONSULTAS_PV,
                    key=f"consulta_pv_{st.session_state.form_key}"
                )
                
                # Mostrar resumen de selección
                if fallas_seleccionadas:
                    st.success(f"✅ {len(fallas_seleccionadas)} consulta(s) seleccionada(s)")
                
                detalle_fallo = st.text_area(
                    "Otras consultas o detalles adicionales",
                    placeholder="Describa su consulta o necesidad...",
                    key=f"detalle_pv_{st.session_state.form_key}",
                    height=100
                )
            
            # FALLA CRÍTICA
            elif motivo == "Cambio por falla de funcionamiento crítica":
                st.markdown("#### Falla de Funcionamiento Crítica")
                st.warning("""
                **¿Qué es una falla crítica?**
                
                Una falla crítica es aquella que impide el uso del equipo de forma segura o efectiva, requiriendo su reemplazo inmediato.
                
                **Ejemplos:**                         
                -El equipo no enciende.
                -Hay riesgo eléctrico, fuego, humo, olor a quemado.
                -El equipo se apaga solo o falla en medio de un uso clínico.
                -El equipo muestra valores erráticos que pueden poner en riesgo al paciente.
                -La falla impide totalmente utilizarlo para su función principal.
                -El problema compromete la seguridad (descargas, piezas sueltas, sobrecalentamiento).
                """)
                
                fallas_seleccionadas = []  # No usar multiselect
                
                detalle_fallo = st.text_area(
                    "Describa la falla crítica *",
                    placeholder="Describa detalladamente la falla que justifica el cambio del equipo. Sea específico sobre por qué es crítica.",
                    key=f"falla_critica_{st.session_state.form_key}",
                    height=150
                )
                               
            diagnostico_paciente = st.text_area(
                "Diagnóstico del Paciente (si aplica)",
                key=f"diagnostico_{st.session_state.form_key}"
            )
            
            data.update({
                'fallas_problemas': fallas_seleccionadas,
                'detalle_fallo': detalle_fallo,
                'diagnostico_paciente': diagnostico_paciente
            })
        
       # SECCIÓN 7: Motivo de Baja (solo para Baja de Alquiler)
        if motivo == "Baja de Alquiler":
            mostrar_seccion_baja_alquiler(data)

        # SECCIÓN 8: Datos de Equipos
        # Solo mostrar si hay motivo Y NO es un equipo de stock o baja de demo (que ya se mostró antes)
        if motivo and data.get('equipo_corresponde_a') not in ["Equipo de Stock", "Baja de demo"]:
            mostrar_seccion_equipos(data, contexto="principal")
                
        
        # Botón de envío
        # Verificar si hay motivo según el tipo de solicitante
        tiene_motivo = False
        
        if quien_completa == "Colaborador de Syemed":
            tiene_motivo = data.get('equipo_corresponde_a') != ""
        else:
            tiene_motivo = data.get('motivo_solicitud') != ""
        
        if tiene_motivo and email_valido:
            # Validar todos los campos obligatorios
            campos_validos, errores_validacion = validar_campos_obligatorios(data)
            
            if not campos_validos:
                #st.markdown('<div class="error-box">', unsafe_allow_html=True)
                st.error("⚠️ Por favor complete todos los campos obligatorios:")
                for error in errores_validacion:
                    st.markdown(f"• {error}")
                st.markdown('</div>', unsafe_allow_html=True)
            
            # ========== NUEVO: SEGURIDAD ==========
            # Mostrar captcha
            captcha_valido = mostrar_captcha()
            # ======================================
            
            st.markdown("---")
            
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                if st.button(
                    "Enviar Solicitud", 
                    use_container_width=True, 
                    type="primary", 
                    disabled=not (campos_validos and captcha_valido),  # ← MODIFICADO
                    key=f"btn_enviar_{st.session_state.form_key}"
                ):
                    # ========== NUEVO: SEGURIDAD ==========
                    # Aplicar validaciones de seguridad
                    seguridad_aprobada, msg_seguridad = aplicar_seguridad_formulario(
                        data=data,
                        archivos_fotos=None,  # Ya no se usan fotos generales
                        archivos_facturas=data.get('facturas')
                    )
                    
                    if not seguridad_aprobada:
                        st.error(msg_seguridad)
                        st.stop()
                    # ======================================
                    
                    # Continuar con el procesamiento normal
                    procesar_formulario(data)
        
        elif not email_valido:
            st.warning("⚠️ Por favor, ingresa un correo electrónico válido antes de enviar.")
    
    elif email and not email_valido:
        st.warning("⚠️ Por favor, corrige el formato del correo electrónico para continuar.")
    else:
        st.info("ℹ️ Por favor complete el correo electrónico y seleccione quién completa la solicitud para continuar.")

def mostrar_resumen_y_descarga():
    """Muestra el resumen después de enviar el formulario"""
    st.markdown("""
    <div class="main-header">
        <h1>✅ Solicitud Enviada Exitosamente</h1>
    </div>
    """, unsafe_allow_html=True)
    
    solicitud_id = st.session_state.get('solicitud_id')
    
    st.success(f"🎉 ¡Tu solicitud #{solicitud_id} ha sido registrada correctamente!")
    
    # Mostrar información
    st.info("""
    📧 **Hemos enviado un correo de confirmación** con todos los detalles de tu solicitud.
    
    Nuestro equipo se pondrá en contacto contigo a la brevedad.
    """)
    
    st.markdown("---")
    
    # Botón de descarga del PDF
    if 'pdf_bytes' in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.download_button(
                label="📥 Descargar PDF de la Solicitud",
                data=st.session_state['pdf_bytes'],
                file_name=st.session_state['pdf_filename'],
                mime="application/pdf",
                use_container_width=True,
                type="primary"
            )
    
    st.markdown("---")
    
    # Botón para nueva solicitud
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button(
            "📝 Crear Nueva Solicitud", 
            use_container_width=True, 
            type="secondary",
            key="btn_nueva_solicitud_final"
        ):
            # Limpiar session_state y aumentar form_key
            keys_to_delete = [k for k in st.session_state.keys() if k != 'form_key']
            for key in keys_to_delete:
                del st.session_state[key]
            
            # Incrementar form_key para regenerar todos los widgets
            st.session_state.form_key += 1
            st.rerun()


# Actualizar las funciones de secciones para incluir keys
def mostrar_seccion_distribuidor(data, es_directo=False):
    """VERSIÓN NUEVA - Reemplaza la función existente"""
    st.markdown(f'<div class="section-header"><h2>Distribuidor</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    col1, col2 = st.columns(2)
    with col1:
        nombre_fantasia = st.text_input("Nombre de Fantasía *", placeholder="Ejemplo: Syemed", key=f"d_nombre_{form_key}")
        razon_social = st.text_input("Razón Social *", placeholder="Ejemplo: Grupo Syemed SRL", key=f"d_razon_{form_key}")
        cuit_input = st.text_input("CUIT * (solo números)", placeholder="30718343832", key=f"d_cuit_{form_key}", max_chars=11)
        cuit = validar_solo_numeros(cuit_input)
        if cuit_input and not cuit_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el CUIT")
        contacto_nombre = st.text_input("Nombre de contacto para Servicio Técnico *", key=f"d_contacto_{form_key}")

    with col2:
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"d_tel_{form_key}", max_chars=15)
        contacto_telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
        comercial_syemed = st.selectbox("Comercial de contacto en Syemed *", COMERCIALES, key=f"d_comercial_{form_key}")
        contacto_tecnico = st.selectbox("¿Quiere que lo contactemos desde el área técnica? *", ["", "Sí", "No"], key=f"d_contacto_tec_{form_key}")
    
    data.update({
        'nombre_fantasia': nombre_fantasia,
        'razon_social': razon_social,
        'cuit': cuit,
        'contacto_nombre': contacto_nombre,
        'contacto_telefono': contacto_telefono,
        'comercial_syemed': comercial_syemed,
        'contacto_tecnico': contacto_tecnico
    })
    
    # NUEVO FLUJO CONDICIONAL
    flujo_data = mostrar_flujo_motivo_solicitud_distribuidor_institucion(data, "d", form_key)
    data.update(flujo_data)

def mostrar_seccion_distribuidorB(data, es_directo=False):
    """VERSIÓN NUEVA - Reemplaza la función existente"""
    st.markdown(f'<div class="section-header"><h2>Ingrese los datos del distribuidor</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    col1, col2 = st.columns(2)
    with col1:
        nombre_fantasia = st.text_input("Nombre de Fantasía *", placeholder="Ejemplo: Syemed", key=f"db_nombre_{form_key}")
        razon_social = st.text_input("Razón Social *", placeholder="Ejemplo: Grupo Syemed SRL", key=f"db_razon_{form_key}")
        cuit_input = st.text_input("CUIT * (solo números)", placeholder="30718343832", key=f"db_cuit_{form_key}", max_chars=11)
        cuit = validar_solo_numeros(cuit_input)
        if cuit_input and not cuit_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el CUIT")
        
    
    with col2:
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"db_tel_{form_key}", max_chars=15)
        contacto_telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
        contacto_tecnico = st.selectbox("¿Quiere que lo contactemos desde el área técnica? *", ["", "Sí", "No"], key=f"db_contacto_tec_{form_key}")
        contacto_nombre = st.text_input("Nombre de contacto para Servicio Técnico *", key=f"db_contacto_{form_key}")

    data.update({
        'nombre_fantasia': nombre_fantasia,
        'razon_social': razon_social,
        'cuit': cuit,
        'contacto_nombre': contacto_nombre,
        'contacto_telefono': contacto_telefono,
        'contacto_tecnico': contacto_tecnico
    })
    
    # NUEVO FLUJO CONDICIONAL
    flujo_data = mostrar_flujo_motivo_solicitud_distribuidor_institucion(data, "db", form_key)
    data.update(flujo_data)


def mostrar_seccion_institucion(data, es_directo=False):
    """VERSIÓN NUEVA - Reemplaza la función existente"""
    st.markdown(f'<div class="section-header"><h2>Institución</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    col1, col2 = st.columns(2)
    with col1:
        nombre_fantasia = st.text_input("Nombre del Hospital/Clínica/Sanatorio *", key=f"i_nombre_{form_key}")
        razon_social = st.text_input("Razón Social *", placeholder="Ejemplo: Grupo Syemed SRL", key=f"i_razon_{form_key}")
        cuit_input = st.text_input("CUIT (solo números)", placeholder="30718343832", key=f"i_cuit_{form_key}", max_chars=11)
        cuit = validar_solo_numeros(cuit_input)
        if cuit_input and not cuit_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el CUIT")
        contacto_nombre = st.text_input("Nombre de contacto para Servicio Técnico *", key=f"i_contacto_{form_key}")
    
    with col2:
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"i_tel_{form_key}", max_chars=15)
        contacto_telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
        comercial_syemed = st.selectbox("Comercial de contacto en Syemed *", COMERCIALES, key=f"i_comercial_{form_key}")
        contacto_tecnico = st.selectbox("¿Quiere que lo contactemos desde el área técnica? *", ["", "Sí", "No"], key=f"i_contacto_tec_{form_key}")
    
    data.update({
        'nombre_fantasia': nombre_fantasia,
        'razon_social': razon_social,
        'cuit': cuit,
        'contacto_nombre': contacto_nombre,
        'contacto_telefono': contacto_telefono,
        'comercial_syemed': comercial_syemed,
        'contacto_tecnico': contacto_tecnico
    })
    
    # NUEVO FLUJO CONDICIONAL
    flujo_data = mostrar_flujo_motivo_solicitud_distribuidor_institucion(data, "i", form_key)
    data.update(flujo_data)


def mostrar_seccion_institucionB(data, es_directo=False):
    """VERSIÓN NUEVA - Reemplaza la función existente"""
    st.markdown(f'<div class="section-header"><h2>Ingrese los datos de la Institución</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    col1, col2 = st.columns(2)
    with col1:
        nombre_fantasia = st.text_input("Nombre del Hospital/Clínica/Sanatorio *", key=f"ib_nombre_{form_key}")
        razon_social = st.text_input("Razón Social *", placeholder="Ejemplo: Grupo Syemed SRL", key=f"ib_razon_{form_key}")
        cuit_input = st.text_input("CUIT * (solo números)", placeholder="30718343832", key=f"ib_cuit_{form_key}", max_chars=11)
        cuit = validar_solo_numeros(cuit_input)
        if cuit_input and not cuit_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el CUIT")
        
    
    with col2:
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"ib_tel_{form_key}", max_chars=15)
        contacto_telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
        contacto_tecnico = st.selectbox("¿Quiere que lo contactemos desde el área técnica? *", ["", "Sí", "No"], key=f"ib_contacto_tec_{form_key}")
        contacto_nombre = st.text_input("Nombre de contacto para Servicio Técnico *", key=f"ib_contacto_{form_key}")
    
    data.update({
        'nombre_fantasia': nombre_fantasia,
        'razon_social': razon_social,
        'cuit': cuit,
        'contacto_nombre': contacto_nombre,
        'contacto_telefono': contacto_telefono,
        'contacto_tecnico': contacto_tecnico
    })
    
    # NUEVO FLUJO CONDICIONAL
    flujo_data = mostrar_flujo_motivo_solicitud_distribuidor_institucion(data, "ib", form_key)
    data.update(flujo_data)




    
def mostrar_seccion_paciente(data, es_directo=False):
    
    st.markdown(f'<div class="section-header"><h2>Paciente/Particular</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key

    col1, col2 = st.columns(2)
    
    with col1:
        nombre_apellido = st.text_input("Nombre y Apellido *", key=f"p_nombreyapellido_{form_key}" )
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"p_telefono_{form_key}", max_chars=15)
        telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
        
    with col2:
        equipo_origen = st.selectbox("El equipo... *", ["", "Lo compró de manera directa", "Se lo entregaron"], key=f"p_equipoorigen_{form_key}" )
        quien_entrego = ""
        if equipo_origen == "Se lo entregaron":
            quien_entrego = st.text_area("¿Quién lo entregó?", placeholder="Obra Social, Distribuidor, Ortopedia, Plataformas Digitales, etc.", key=f"p_quienentrego_{form_key}" )
        
        motivo_solicitud = st.selectbox("Motivo de la solicitud *", ["", "Servicio Técnico (reparaciones de equipos en general)", "Servicio de Asistencia Técnica (para nuestros productos adquiridos)", "Baja de Alquiler", "Cambio por falla de funcionamiento crítica"], key=f"p_motivosolicitud_{form_key}")
        # Normalizar el valor para compatibilidad con BD
        motivo_solicitud = normalizar_motivo_solicitud(motivo_solicitud)
    
    data.update({
        'nombre_apellido_paciente': nombre_apellido,
        'telefono_paciente': telefono,
        'equipo_origen': equipo_origen,
        'quien_entrego': quien_entrego,
        'motivo_solicitud': motivo_solicitud
    })
def mostrar_seccion_baja_alquiler(data):
    """Muestra la sección condicional para motivo de baja en alquileres"""
    st.markdown('<div class="section-header"><h2>Motivo de Baja de Alquiler</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    fin_contrato = st.selectbox(
        "¿Es por fin de contrato? *",
        ["", "Sí", "No"],
        key=f"fin_contrato_{form_key}"
    )
    
    equipo_falla = None
    tipo_falla = None
    motivo_baja_otro = None
    motivo_baja = ""
    observacion_baja = ""
    estado_equipo = ""
    
    if fin_contrato == "Sí":
        equipo_falla = st.selectbox(
            "¿El equipo falla? *",
            ["", "Sí", "No"],
            key=f"equipo_falla_fin_{form_key}"
        )
        
        if equipo_falla == "Sí":
            tipo_falla = st.text_area(
                "Describa el tipo de falla *",
                height=100,
                placeholder="Describa detalladamente la falla presentada...",
                key=f"tipo_falla_fin_{form_key}"
            )
            motivo_baja = "Fin de contrato"
            observacion_baja = tipo_falla if tipo_falla else ""
            estado_equipo = "Con falla"
        elif equipo_falla == "No":
            motivo_baja = "Fin de contrato"
            estado_equipo = "Funcional"
    
    elif fin_contrato == "No":
        equipo_falla = st.selectbox(
            "¿El equipo falla? *",
            ["", "Sí", "No"],
            key=f"equipo_falla_no_fin_{form_key}"
        )
        
        if equipo_falla == "Sí":
            tipo_falla = st.text_area(
                "Describa el tipo de falla *",
                height=100,
                placeholder="Describa detalladamente la falla presentada...",
                key=f"tipo_falla_no_fin_{form_key}"
            )
            motivo_baja = "Falla en el equipo"
            observacion_baja = tipo_falla if tipo_falla else ""
            estado_equipo = "Con falla"
        elif equipo_falla == "No":
            motivo_baja_otro = st.text_area(
                "Comente el motivo de baja *",
                height=100,
                placeholder="Indique el motivo por el cual solicita la baja del alquiler...",
                key=f"motivo_baja_otro_{form_key}"
            )
            motivo_baja = "Otros motivos"
            observacion_baja = motivo_baja_otro if motivo_baja_otro else ""
            estado_equipo = "Funcional"
    
    data.update({
        'fin_contrato': fin_contrato,
        'equipo_falla': equipo_falla,
        'tipo_falla': tipo_falla,
        'motivo_baja_otro': motivo_baja_otro,
        'motivo_baja': motivo_baja,
        'observacion_baja': observacion_baja,
        'estado_equipo': estado_equipo
    })

def mostrar_seccion_equipos(data, contexto="general"):
    st.markdown('<div class="section-header"><h2>Datos de los Equipos</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key
    
    # Verificar si el motivo permite múltiples equipos
    motivo_solicitud = data.get('motivo_solicitud', '')
    permite_multiples = motivo_solicitud in ["Baja de Alquiler", "Cambio de Alquiler", "Equipo de Stock", "Baja de demo"]
    
    # Solo mostrar opción de modo de carga si está permitido
    if permite_multiples:
        modo_carga = st.radio(
            "¿Cómo desea cargar los equipos?",
            ["Equipos individuales (diferentes características)", "Múltiples equipos similares (mismo tipo, marca, modelo)"],
            index=0,
            key=f"modo_carga_{contexto}_{form_key}"
        )
    else:
        # Forzar modo individual para otros motivos
        modo_carga = "Equipos individuales (diferentes características)"

    
    equipos = []
    
    if modo_carga == "Equipos individuales (diferentes características)":
        num_equipos = st.number_input("¿Cuántos equipos desea registrar?", min_value=1, max_value=100, value=1, key=f"num_equipos_{contexto}_{form_key}")
        
        for i in range(num_equipos):
            st.markdown(f'<div class="equipment-section"><h3>Equipo {i+1}</h3>', unsafe_allow_html=True)
            
            col1, col2 = st.columns(2)
            with col1:
                tipo_equipo = st.selectbox(f"Tipo de Equipo ({i+1}) *", TIPOS_EQUIPO, key=f"tipo_{contexto}_{i}_{form_key}")
                marca_equipo = st.selectbox(f"Marca de Equipo ({i+1}) *", MARCAS_EQUIPO, key=f"marca_{contexto}_{i}_{form_key}")
                
            with col2:
                modelo_equipo = st.selectbox(f"Modelo de Equipo ({i+1}) *", MODELOS_EQUIPO, key=f"modelo_{contexto}_{i}_{form_key}")
                numero_serie = st.text_input(f"Número de Serie ({i+1}) *", key=f"serie_{contexto}_{i}_{form_key}")
                
                # CAMBIO: Obtener garantía desde Información del Equipo (data)
                # Ya no se pregunta aquí, se obtiene de la sección anterior
                en_garantia_global = data.get('en_garantia', None)
                
                # Determinar si está en garantía desde data
                if en_garantia_global == "Sí":
                    en_garantia = "Sí"
                    # La fecha de compra y factura se cargan en "Información del Equipo", no aquí
                    fecha_compra = data.get('fecha_compra', None)
                    factura_archivo = None  # Ya no se carga aquí
                else:
                    en_garantia = "No"
                    fecha_compra = None
                    factura_archivo = None
            
            # Fotos/videos de fallas por equipo
            motivo = data.get('motivo_solicitud', '')
            fotos_equipo = []
            if motivo in ["Servicio Técnico (reparaciones de equipos en general)", 
              "Servicio Post Venta (para alguno de nuestros productos adquiridos)", 
              "Cambio por falla de funcionamiento crítica"]:
                st.markdown(f"**📸 Fotos/videos de fallas del Equipo {i+1}** (opcional)")
                fotos_equipo_raw = st.file_uploader(
                    f"Adjunte fotos o videos del problema del Equipo {i+1}",
                    type=['jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4', 'mov', 'avi', 'mkv'],
                    accept_multiple_files=True,
                    key=f"fotos_equipo_{contexto}_{i}_{form_key}",
                    help="Puede adjuntar múltiples archivos del mismo equipo (incluyendo imágenes de WhatsApp)"
                )
                if fotos_equipo_raw:
                    for archivo in fotos_equipo_raw:
                        partes = archivo.name.lower().split('.')
                        extension_real = partes[-1] if len(partes) > 1 else ''
                        extensiones_validas = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4', 'mov', 'avi', 'mkv']
                        if extension_real in extensiones_validas:
                            fotos_equipo.append(archivo)
                        else:
                            st.warning(f"⚠️ Archivo '{archivo.name}' no tiene una extensión válida")
                if fotos_equipo:
                    st.info(f"📎 {len(fotos_equipo)} archivo(s) para este equipo")

            # ==============================================================
            
            equipos.append({
                'tipo_equipo': tipo_equipo,
                'marca': marca_equipo,
                'modelo': modelo_equipo,
                'numero_serie': numero_serie,
                'en_garantia': en_garantia == "Sí",
                'fecha_compra': fecha_compra,
                'fotos_fallas': fotos_equipo  # ← NUEVO
            })
            
            st.markdown('</div>', unsafe_allow_html=True)
    
    else:
        # Modo múltiples equipos similares
        st.markdown('<div class="equipment-section"><h3>Información Común de los Equipos</h3>', unsafe_allow_html=True)
        
        # Mensaje informativo sobre fotos/videos deshabilitados
        motivo_solicitud = data.get('motivo_solicitud', '')
        if motivo_solicitud in ["Baja de Alquiler", "Cambio de Alquiler", "Equipo de Stock", "Baja de demo"]:
            st.info("ℹ️ Al cargar múltiples equipos a la vez, puede listar todos los números de serie con saltos de línea a o comas.")
        
        col1, col2 = st.columns(2)
        with col1:
            tipo_equipo_comun = st.selectbox("Tipo de Equipo *", TIPOS_EQUIPO, key=f"tipo_comun_{contexto}_{form_key}")
            modelo_equipo_comun = st.selectbox("Modelo de Equipo *", MODELOS_EQUIPO, key=f"modelo_comun_{contexto}_{form_key}")
        
        with col2:
            marca_equipo_comun = st.selectbox("Marca de Equipo *", MARCAS_EQUIPO, key=f"marca_comun_{contexto}_{form_key}")
            
            # CAMBIO: Obtener garantía desde Información del Equipo (data)
            en_garantia_global = data.get('en_garantia', None)
            
            if en_garantia_global == "Sí":
                en_garantia_comun = "Sí"
            else:
                en_garantia_comun = "No"
        
        # La fecha de compra y factura se cargan en "Información del Equipo", no aquí
        fecha_compra_comun = data.get('fecha_compra', None)
        factura_comun = None  # Ya no se carga aquí
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Números de serie
        st.markdown('<div class="equipment-section"><h3>Números de Serie</h3>', unsafe_allow_html=True)
        
        metodo_serie = st.radio(
            "¿Cómo desea ingresar los números de serie?",
            ["Uno por uno", "Lista separada por comas/saltos de línea"],
            key=f"metodo_serie_{contexto}_{form_key}"
        )
        
        numeros_serie = []
        
        if metodo_serie == "Uno por uno":
            num_series = st.number_input("¿Cuántos números de serie?", min_value=1, max_value=100, value=1, key=f"num_series_{contexto}_{form_key}")
            
            for i in range(num_series):
                serie = st.text_input(f"Número de Serie {i+1} *", key=f"serie_multiple_{contexto}_{i}_{form_key}")
                if serie.strip():
                    numeros_serie.append(serie.strip())
        
        else:
            series_texto = st.text_area(
                "Ingrese todos los números de serie separados por comas o saltos de línea *",
                height=150,
                placeholder="Ejemplo:\nSYE001\nSYE002, SYE003\nSYE004",
                key=f"series_masivo_{contexto}_{form_key}"
            )
            
            if series_texto:
                import re
                numeros_serie = [
                    serie.strip() 
                    for serie in re.split(r'[,;\n\r]+', series_texto) 
                    if serie.strip()
                ]
                
                if numeros_serie:
                    st.info(f"Se detectaron {len(numeros_serie)} números de serie:")
                    num_cols = min(3, len(numeros_serie))
                    cols = st.columns(num_cols)
                    
                    for i, serie in enumerate(numeros_serie[:15]):
                        with cols[i % num_cols]:
                            st.text(f"• {serie}")
                    
                    if len(numeros_serie) > 15:
                        st.text(f"... y {len(numeros_serie) - 15} más")
        
        # Crear equipos
        for numero_serie in numeros_serie:
            if numero_serie:
                equipos.append({
                    'tipo_equipo': tipo_equipo_comun,
                    'marca': marca_equipo_comun,
                    'modelo': modelo_equipo_comun,
                    'numero_serie': numero_serie,
                    'en_garantia': en_garantia_comun == "Sí",
                    'fecha_compra': fecha_compra_comun
                })
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        if equipos:
            st.success(f"✅ Total de equipos que se registrarán: **{len(equipos)}**")
    
    data['equipos'] = equipos

def mostrar_seccion_paciente(data, es_directo=False):
    """VERSIÓN NUEVA - Reemplaza la función existente"""
    st.markdown(f'<div class="section-header"><h2>Paciente/Particular</h2></div>', unsafe_allow_html=True)
    
    form_key = st.session_state.form_key

    col1, col2 = st.columns(2)
    
    with col1:
        nombre_apellido = st.text_input("Nombre y Apellido *", key=f"p_nombreyapellido_{form_key}")
        
    with col2:
        telefono_input = st.text_input("Teléfono de contacto * (solo números)", placeholder="1123730278", key=f"p_telefono_{form_key}", max_chars=15)
        telefono = validar_solo_numeros(telefono_input)
        if telefono_input and not telefono_input.isdigit():
            st.warning("⚠️ Solo se permiten números en el teléfono")
    
    data.update({
        'nombre_apellido_paciente': nombre_apellido,
        'telefono_paciente': telefono
    })
    
    # NUEVO FLUJO CONDICIONAL
    flujo_data = mostrar_flujo_motivo_solicitud_paciente(data, form_key)
    data.update(flujo_data)

def procesar_formulario(data):
    """Procesar formulario incluyendo subida de archivos"""
    
    # Validaciones finales
    equipos_validos = [
        eq for eq in data.get('equipos', []) 
        if eq.get('tipo_equipo') != "Seleccionar tipo..." and eq.get('numero_serie')
    ]
    
    data['equipos'] = equipos_validos
    
    # NUEVO: Procesar archivos antes de insertar
    urls_archivos = []
    
    with st.spinner("Subiendo archivos adjuntos..."):
        # Subir fotos/videos por equipo
        for i, equipo in enumerate(data.get('equipos', []), 1):
            if 'fotos_fallas' in equipo and equipo['fotos_fallas']:
                for archivo in equipo['fotos_fallas']:
                    exito, resultado = subir_archivo_cloudinary(archivo, "solicitudes_st/fotos")
                    if exito:
                        urls_archivos.append({
                            'tipo': 'foto_video',
                            'equipo_num': i,
                            'nombre': archivo.name,
                            'url': resultado,
                            'tamano': archivo.size
                        })
        
        # MODIFICADO: Subir factura desde factura_garantia (capturada en Información del Equipo)
        # Esta factura es la misma para todos los equipos
        factura_url_global = None
        if 'factura_garantia' in data and data['factura_garantia']:
            factura = data['factura_garantia']
            exito, resultado = subir_archivo_cloudinary(factura, "solicitudes_st/facturas")
            if exito:
                factura_url_global = resultado
                urls_archivos.append({
                    'tipo': 'factura',
                    'equipo_num': 'todos',  # Se aplica a todos los equipos
                    'nombre': factura.name,
                    'url': resultado,
                    'tamano': factura.size
                })
        
        # NUEVO: Agregar URL de factura a cada equipo
        for equipo in data.get('equipos', []):
            equipo['factura_url'] = factura_url_global
    
    # Agregar URLs a data
    data['archivos_urls'] = urls_archivos
    
    # 1. GUARDAR SOLICITUD EN BD PRIMERO (sin PDF)
    with st.spinner("💾 Guardando solicitud en base de datos..."):
        exito, resultado, equipos_osts = insertar_solicitud(data, pdf_url=None)
    
    if not exito:
        st.error(f"❌ Error al guardar la solicitud: {resultado}")
        return
    
    solicitud_id = resultado
    st.session_state['formulario_enviado'] = True
    st.session_state['solicitud_id'] = solicitud_id
    
    # Mostrar OSTs generados si existen
    if equipos_osts:
        osts_texto = ', '.join([f'#{ost}' for ost in equipos_osts])
        st.success(f"✅ Solicitud #{solicitud_id} guardada correctamente! OST(s): {osts_texto}")
    else:
        st.success(f"✅ Solicitud #{solicitud_id} guardada correctamente!")
    
    # 2. GENERAR PDF CON EL ID CORRECTO Y LOS OSTs
    try:
        with st.spinner("📄 Generando PDF..."):
            pdf_bytes = generar_pdf_solicitud(data, solicitud_id=solicitud_id, equipos_osts=equipos_osts)
            pdf_filename = f"solicitud_ST_{solicitud_id}_{ahora_buenos_aires().strftime('%Y%m%d_%H%M%S')}.pdf"
    except Exception as e:
        st.error(f"❌ Error al generar PDF: {e}")
        return
    
    # 3. SUBIR PDF A CLOUDINARY Y ACTUALIZAR BD
    pdf_url = None
    try:
        with st.spinner("☁️ Subiendo PDF a la nube..."):
            exito_pdf, resultado_pdf = subir_pdf_bytes_cloudinary(
                pdf_bytes=pdf_bytes,
                nombre_archivo=pdf_filename.replace('.pdf', ''),
                carpeta="solicitudes_st/pdfs"
            )
            
            if exito_pdf:
                pdf_url = resultado_pdf
                # Actualizar BD con la URL del PDF
                conn = None
                try:
                    conn = psycopg2.connect(DATABASE_URL)
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE solicitudes SET pdf_url = %s WHERE id = %s",
                        (pdf_url, solicitud_id)
                    )
                    conn.commit()
                    cursor.close()
                    st.success("✅ PDF guardado en la nube")
                except Exception as e:
                    if conn:
                        conn.rollback()
                    st.warning(f"⚠️ Error al actualizar PDF en BD: {e}")
                finally:
                    if conn:
                        conn.close()
            else:
                st.warning(f"⚠️ No se pudo guardar PDF: {resultado_pdf}")
    except Exception as e:
        st.warning(f"⚠️ Error al subir PDF: {e}")
    
    # Mostrar link al PDF si se guardó
    if pdf_url:
        st.info(f"📄 PDF disponible en: {pdf_url[:60]}...")
    
    # Guardar PDF en session_state para descarga
    st.session_state['pdf_bytes'] = pdf_bytes
    st.session_state['pdf_filename'] = pdf_filename
    
    # 4. ENVIAR EMAIL CON PDF
    try:
        with st.spinner("📧 Enviando confirmación por email..."):
            email_enviado, mensaje_email = enviar_email_con_pdf(
                destinatario=data.get('email'),
                solicitud_id=solicitud_id,
                pdf_bytes=pdf_bytes,
                data=data,
                equipos_osts=equipos_osts
            )
            
            if email_enviado:
                st.success("✅ Email de confirmación enviado")
            else:
                st.warning(f"⚠️ {mensaje_email}")
                st.info("La solicitud fue guardada correctamente.")
    except Exception as e:
        st.warning(f"⚠️ Error al enviar email: {e}")
        st.info("La solicitud fue guardada correctamente.")
    
    st.rerun()
  
if __name__ == "__main__":
    main()