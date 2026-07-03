import json
import logging
import base64
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from datetime import datetime
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# URL base para validación de timbre DGII
DGII_TIMBRE_URL = "https://ecf.dgii.gov.do/certecf/consultatimbre"
# URL para validación de RFCE (Factura Consumo < 250k) - usa ConsultaTimbreFC
DGII_TIMBRE_FC_URL = "https://ecf.dgii.gov.do/certecf/ConsultaTimbreFC"


class EcfApiLog(models.Model):
    _name = "ecf.api.log"
    _description = "Log de Llamadas API e-CF"
    _order = "create_date desc, id desc"

    # ========================================================================
    # Identificación y Origen
    # ========================================================================
    name = fields.Char(
        string="Nombre",
        compute="_compute_name",
        store=True
    )

    # Origen de la transacción
    origin = fields.Selection([
        ('simulation', 'Simulador de Documentos'),
        ('test_set', 'Set de Pruebas'),
        ('test_case', 'Caso de Prueba'),
        ('acecf_case', 'Caso ACECF'),
        ('acecf_set', 'Set ACECF'),
        ('acecf_import', 'Importacion ACECF'),
        ('wizard', 'Wizard'),
        ('manual', 'Manual'),
        ('invoice', 'Factura'),
        ('callback_recepcion', 'Callback Recepcion DGII'),
        ('callback_recepcion_json', 'Callback Recepcion (JSON)'),
        ('callback_aprobacion', 'Callback Aprobacion Comercial'),
        ('other', 'Otro'),
    ], string="Origen", default='other', index=True)

    # Relación con caso de prueba
    test_case_id = fields.Many2one(
        "ecf.test.case",
        string="Caso de Prueba",
        ondelete="set null",
        index=True
    )

    # Relación con caso de aprobación comercial ACECF
    acecf_case_id = fields.Many2one(
        "acecf.case",
        string="Caso ACECF",
        ondelete="set null",
        index=True
    )

    # Relación con documento de simulación
    simulation_doc_id = fields.Many2one(
        "ecf.simulation.document",
        string="Documento Simulación",
        ondelete="set null",
        index=True
    )

    # Proveedor de API usado
    provider_id = fields.Many2one(
        "ecf.api.provider",
        string="Proveedor API",
        ondelete="set null",
        index=True
    )
    provider_name = fields.Char(string="Nombre Proveedor")
    provider_type = fields.Char(string="Tipo Proveedor")

    # Información del envío
    id_lote = fields.Char(string="ID Lote", index=True)
    fila_excel = fields.Integer(string="Fila Excel")

    # ========================================================================
    # Datos del Documento e-CF
    # ========================================================================
    rnc_emisor = fields.Char(string="RNC Emisor", index=True)
    encf = fields.Char(string="eNCF", index=True)
    tipo_ecf = fields.Char(string="Tipo e-CF")

    # ========================================================================
    # Request (Envío)
    # ========================================================================
    request_url = fields.Char(string="URL")
    request_method = fields.Char(string="Método HTTP", default="POST")
    request_headers = fields.Text(string="Headers Request")
    request_payload = fields.Text(string="Payload Enviado")
    request_payload_formatted = fields.Text(
        string="Payload Formateado",
        compute="_compute_formatted_fields"
    )
    request_timestamp = fields.Datetime(
        string="Fecha/Hora Envío",
        default=fields.Datetime.now,
        index=True
    )

    # ========================================================================
    # Response (Respuesta)
    # ========================================================================
    response_status_code = fields.Integer(string="Código HTTP")
    response_headers = fields.Text(string="Headers Response")
    response_body = fields.Text(string="Respuesta Completa (Raw)")
    response_body_formatted = fields.Text(
        string="Respuesta Formateada",
        compute="_compute_formatted_fields"
    )
    response_json = fields.Text(string="Respuesta JSON Parseada")
    response_timestamp = fields.Datetime(string="Fecha/Hora Respuesta")
    response_time_ms = fields.Integer(
        string="Tiempo Respuesta (ms)",
        help="Tiempo de respuesta en milisegundos"
    )

    # XML Recibido (para callbacks: XML original que llegó de DGII)
    incoming_xml = fields.Text(
        string="XML Recibido",
        help="XML original recibido en un callback de DGII (e-CF que nos enviaron)."
    )

    # XML Firmado (se guarda original sin formatear para no invalidar firma)
    # Para RFCE: signed_xml = XML RFCE (resumen), signed_xml_ecf = XML ECF completo
    signed_xml = fields.Text(
        string="XML Firmado",
        help="XML firmado devuelto por la API. Para RFCE es el XML de resumen."
    )
    signed_xml_ecf = fields.Text(
        string="XML ECF Completo",
        help="Para facturas RFCE (consumo < 250k): XML ECF completo firmado que se guarda internamente."
    )
    is_rfce = fields.Boolean(
        string="Es RFCE",
        default=False,
        help="Indica si este documento fue enviado como RFCE (resumen de consumo < 250k)"
    )
    has_signed_xml = fields.Boolean(
        string="Tiene XML",
        compute="_compute_has_signed_xml",
        store=True
    )
    has_incoming_xml = fields.Boolean(
        string="Tiene XML Recibido",
        compute="_compute_has_incoming_xml",
        store=True
    )

    # ========================================================================
    # Datos extraídos del XML Firmado para URL de Validación
    # ========================================================================
    xml_rnc_emisor = fields.Char(string="RNC Emisor (XML)")
    xml_rnc_comprador = fields.Char(string="RNC Comprador (XML)")
    xml_encf = fields.Char(string="eNCF (XML)")
    xml_fecha_emision = fields.Char(string="Fecha Emisión (XML)")
    xml_monto_total = fields.Char(string="Monto Total (XML)")
    xml_fecha_firma = fields.Char(string="Fecha/Hora Firma (XML)")
    xml_security_code = fields.Char(string="Código Seguridad (XML)")

    # Código de seguridad ECF (de la respuesta API, para RFCE)
    ecf_security_code = fields.Char(
        string="Código Seguridad ECF",
        help="Código de seguridad del ECF completo (ecfSecurityCode de la API). "
             "Para RFCE se usa este código en la URL de validación."
    )

    # URL de validación DGII
    dgii_validation_url = fields.Char(
        string="URL Validación DGII",
        compute="_compute_dgii_validation_url",
        store=True
    )

    # ========================================================================
    # Estado y Resultado
    # ========================================================================
    track_id = fields.Char(string="Track ID", index=True)
    api_status = fields.Selection([
        ('pending', 'Pendiente'),
        ('success', 'Exitoso'),
        ('error', 'Error HTTP'),
        ('timeout', 'Timeout'),
        ('connection_error', 'Error de Conexión'),
        ('accepted', 'Aceptado DGII'),
        ('rejected', 'Rechazado DGII'),
    ], string="Estado", default='pending', index=True)

    success = fields.Boolean(string="Exitoso", default=False, index=True)

    # Mensajes y errores
    error_message = fields.Text(string="Mensaje de Error")
    dgii_message = fields.Text(string="Mensaje DGII")
    dgii_code = fields.Char(string="Código DGII")

    # Conexión exitosa
    connection_success = fields.Boolean(
        string="Conexión Exitosa",
        compute="_compute_connection_success",
        store=True,
        help="Indica si se estableció conexión con la API (HTTP 200-299)"
    )

    # ========================================================================
    # Métodos Computados
    # ========================================================================

    @api.depends('encf', 'tipo_ecf', 'create_date')
    def _compute_name(self):
        for log in self:
            parts = []
            if log.encf:
                parts.append(log.encf)
            elif log.tipo_ecf:
                parts.append(f"Tipo {log.tipo_ecf}")
            if log.create_date:
                parts.append(log.create_date.strftime('%Y-%m-%d %H:%M:%S'))
            log.name = " - ".join(parts) if parts else f"Log {log.id}"

    @api.depends('response_status_code')
    def _compute_connection_success(self):
        for log in self:
            log.connection_success = bool(
                log.response_status_code and
                200 <= log.response_status_code < 300
            )

    @api.depends('signed_xml')
    def _compute_has_signed_xml(self):
        for log in self:
            log.has_signed_xml = bool(log.signed_xml)

    @api.depends('incoming_xml')
    def _compute_has_incoming_xml(self):
        for log in self:
            log.has_incoming_xml = bool(log.incoming_xml)

    @api.depends('xml_rnc_emisor', 'xml_rnc_comprador', 'xml_encf',
                 'xml_fecha_emision', 'xml_monto_total', 'xml_fecha_firma', 'xml_security_code',
                 'is_rfce', 'ecf_security_code')
    def _compute_dgii_validation_url(self):
        """
        Construye la URL de validación DGII a partir de los datos del XML.

        Para RFCE (Factura Consumo < 250k) usa URL y parámetros diferentes:
        - URL: https://ecf.dgii.gov.do/certecf/ConsultaTimbreFC
        - Parámetros: RncEmisor, ENCF, MontoTotal, CodigoSeguridad (solo 4, sin fechas)
        - IMPORTANTE: Usa ecfSecurityCode (del ECF), NO rfceSecurityCode (del XML RFCE)

        Para e-CF normales:
        - URL: https://ecf.dgii.gov.do/certecf/consultatimbre
        - Parámetros: rncemisor, RncComprador, encf, fechaemision, montototal, fechafirma, codigoseguridad
        """
        for log in self:
            _logger.info(f"[DGII URL] ========== Calculando URL para log {log.id} ==========")
            _logger.info(f"[DGII URL] is_rfce={log.is_rfce}, encf={log.encf}")
            _logger.info(f"[DGII URL] ecf_security_code={log.ecf_security_code}")
            _logger.info(f"[DGII URL] xml_security_code={log.xml_security_code}")

            # Para RFCE: URL y parámetros simplificados
            if log.is_rfce:
                # Para RFCE: usar ecf_security_code (del ECF completo), no xml_security_code (del RFCE)
                security_code = log.ecf_security_code or log.xml_security_code
                _logger.info(f"[DGII URL] RFCE - security_code seleccionado: {security_code}")

                if not all([log.xml_rnc_emisor, log.xml_encf, log.xml_monto_total, security_code]):
                    _logger.warning(f"[DGII URL] RFCE - Faltan datos: rnc={log.xml_rnc_emisor}, encf={log.xml_encf}, monto={log.xml_monto_total}, code={security_code}")
                    log.dgii_validation_url = False
                    continue

                # RFCE solo necesita 4 parámetros (sin fechas)
                params = {
                    'RncEmisor': log.xml_rnc_emisor,
                    'ENCF': log.xml_encf,
                    'MontoTotal': log.xml_monto_total,
                    'CodigoSeguridad': security_code,
                }

                query = urlencode(params).replace("+", "%20")
                url = f"{DGII_TIMBRE_FC_URL}?{query}"
                _logger.info(f"[DGII URL] RFCE - URL generada: {url}")
                log.dgii_validation_url = url
            else:
                # e-CF normal: todos los parámetros
                if not all([log.xml_rnc_emisor, log.xml_encf, log.xml_fecha_emision,
                           log.xml_monto_total, log.xml_fecha_firma, log.xml_security_code]):
                    log.dgii_validation_url = False
                    continue

                params = {
                    'rncemisor': log.xml_rnc_emisor,
                    'RncComprador': log.xml_rnc_comprador or '',
                    'encf': log.xml_encf,
                    'fechaemision': log.xml_fecha_emision,
                    'montototal': log.xml_monto_total,
                    'fechafirma': log.xml_fecha_firma,
                    'codigoseguridad': log.xml_security_code,
                }

                query = urlencode(params).replace("+", "%20")
                log.dgii_validation_url = f"{DGII_TIMBRE_URL}?{query}"

    @api.depends('request_payload', 'response_body')
    def _compute_formatted_fields(self):
        for log in self:
            # Formatear payload
            log.request_payload_formatted = log.format_json(log.request_payload)
            # Formatear respuesta
            log.response_body_formatted = log.format_json(log.response_body)

    # ========================================================================
    # Métodos de Utilidad
    # ========================================================================

    def format_json(self, json_text):
        """Formatea un JSON para mejor visualización"""
        if not json_text:
            return ""
        try:
            obj = json.loads(json_text)
            return json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            return json_text

    def _find_xml_text(self, root, tag_name):
        """
        Busca el primer nodo con ese nombre ignorando namespaces.
        """
        for el in root.iter():
            if el.tag.split("}")[-1] == tag_name:
                return (el.text or "").strip()
        return ""

    def extract_data_from_signed_xml(self):
        """
        Extrae los datos necesarios del XML firmado para construir la URL de validación.
        Se puede llamar manualmente o se llama automáticamente al guardar signed_xml.
        """
        self.ensure_one()
        if not self.signed_xml:
            return False

        try:
            root = ET.fromstring(self.signed_xml)

            # Extraer datos del XML
            rnc_emisor = self._find_xml_text(root, "RNCEmisor")
            rnc_comprador = self._find_xml_text(root, "RNCComprador")
            encf = self._find_xml_text(root, "eNCF")
            fecha_emision = self._find_xml_text(root, "FechaEmision")
            monto_total = self._find_xml_text(root, "MontoTotal")
            fecha_firma = self._find_xml_text(root, "FechaHoraFirma")

            # El código de seguridad son los primeros 6 caracteres del SignatureValue
            signature_value = self._find_xml_text(root, "SignatureValue")
            security_code = signature_value[:6] if signature_value else ""

            # Actualizar campos
            self.write({
                'xml_rnc_emisor': rnc_emisor,
                'xml_rnc_comprador': rnc_comprador,
                'xml_encf': encf,
                'xml_fecha_emision': fecha_emision,
                'xml_monto_total': monto_total,
                'xml_fecha_firma': fecha_firma,
                'xml_security_code': security_code,
            })

            _logger.info(f"[API Log] Datos extraídos del XML para log {self.id}: eNCF={encf}, SecurityCode={security_code}")
            return True

        except ET.ParseError as e:
            _logger.error(f"[API Log] Error parseando XML firmado: {e}")
            return False
        except Exception as e:
            _logger.error(f"[API Log] Error extrayendo datos del XML: {e}")
            return False

    def action_extract_xml_data(self):
        """Acción para extraer datos del XML manualmente"""
        self.ensure_one()

        # Primero intentar extraer ecfSecurityCode del response_json guardado (para RFCE)
        if self.is_rfce and self.response_json and not self.ecf_security_code:
            try:
                response_data = json.loads(self.response_json)
                data_obj = response_data.get('data', response_data)
                ecf_security_code = data_obj.get('ecfSecurityCode')
                if ecf_security_code:
                    self.write({'ecf_security_code': ecf_security_code})
                    _logger.info(f"[API Log] ecfSecurityCode extraído del response_json: {ecf_security_code}")
            except Exception as e:
                _logger.warning(f"[API Log] No se pudo extraer ecfSecurityCode del response: {e}")

        if self.extract_data_from_signed_xml():
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Datos Extraídos"),
                    'message': _("Se extrajeron los datos del XML firmado correctamente."),
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            raise UserError(_("No se pudo extraer los datos del XML. Verifique que el XML sea válido."))

    def action_open_validation_url(self):
        """Abre la URL de validación DGII en una nueva pestaña"""
        self.ensure_one()
        if not self.dgii_validation_url:
            raise UserError(_("No hay URL de validación disponible. Primero extraiga los datos del XML."))

        return {
            'type': 'ir.actions.act_url',
            'url': self.dgii_validation_url,
            'target': 'new',
        }

    # ========================================================================
    # Método Principal para Crear Logs
    # ========================================================================

    @api.model
    def create_from_request(self, provider=None, origin='other',
                            test_case_id=None, simulation_doc_id=None, acecf_case_id=None,
                            request_url=None, request_payload=None, request_headers=None,
                            rnc=None, encf=None, tipo_ecf=None, incoming_xml=None):
        """
        Crea un registro de log ANTES de enviar a la API.
        Retorna el log para actualizarlo despues con la respuesta.

        Para callbacks de DGII, se puede pasar incoming_xml con el XML original
        que llego, para trazabilidad y depuracion.
        """
        vals = {
            'origin': origin,
            'request_url': request_url,
            'request_timestamp': fields.Datetime.now(),
            'rnc_emisor': rnc,
            'encf': encf,
            'tipo_ecf': tipo_ecf,
            'api_status': 'pending',
        }

        # Guardar XML original recibido (para callbacks)
        if incoming_xml:
            vals['incoming_xml'] = incoming_xml

        if test_case_id:
            vals['test_case_id'] = test_case_id
        if simulation_doc_id:
            vals['simulation_doc_id'] = simulation_doc_id
        if acecf_case_id:
            vals['acecf_case_id'] = acecf_case_id

        if provider:
            vals.update({
                'provider_id': provider.id,
                'provider_name': provider.name,
                'provider_type': provider.provider_type,
                'request_url': request_url or provider.api_url,
            })

        if request_payload:
            if isinstance(request_payload, dict):
                vals['request_payload'] = json.dumps(request_payload, ensure_ascii=False)
            else:
                vals['request_payload'] = request_payload

        if request_headers:
            if isinstance(request_headers, dict):
                # Ocultar tokens/passwords en headers
                safe_headers = {}
                for k, v in request_headers.items():
                    k_lower = k.lower()
                    if any(x in k_lower for x in ['auth', 'token', 'password', 'key', 'secret']):
                        safe_headers[k] = '***HIDDEN***'
                    else:
                        safe_headers[k] = v
                vals['request_headers'] = json.dumps(safe_headers, ensure_ascii=False)
            else:
                vals['request_headers'] = request_headers

        return self.create(vals)

    def update_with_response(self, success=False, status_code=None,
                             response_body=None, response_json=None,
                             response_headers=None, track_id=None,
                             signed_xml=None, signed_xml_ecf=None,
                             is_rfce=False, ecf_security_code=None,
                             error_message=None, response_time_ms=None):
        """
        Actualiza el log con los datos de la respuesta.

        Para documentos RFCE (consumo < 250k):
        - signed_xml: XML RFCE firmado (el resumen enviado a DGII)
        - signed_xml_ecf: XML ECF completo firmado (para archivo interno)
        - ecf_security_code: Código de seguridad del ECF (ecfSecurityCode de la API)
        """
        self.ensure_one()

        # Determinar estado
        if success:
            api_status = 'accepted'
        elif status_code and 200 <= status_code < 300:
            api_status = 'success'
        elif error_message and 'timeout' in error_message.lower():
            api_status = 'timeout'
        elif error_message and 'conexión' in error_message.lower():
            api_status = 'connection_error'
        else:
            api_status = 'rejected' if status_code else 'error'

        vals = {
            'response_timestamp': fields.Datetime.now(),
            'success': success,
            'api_status': api_status,
            'response_status_code': status_code,
            'track_id': track_id,
            'error_message': error_message,
            'response_time_ms': response_time_ms,
            'is_rfce': is_rfce,
        }

        if response_body:
            vals['response_body'] = response_body

        if response_json:
            if isinstance(response_json, dict):
                vals['response_json'] = json.dumps(response_json, indent=2, ensure_ascii=False)
            else:
                vals['response_json'] = response_json

        if response_headers:
            if isinstance(response_headers, dict):
                vals['response_headers'] = json.dumps(dict(response_headers), ensure_ascii=False)
            else:
                vals['response_headers'] = str(response_headers)

        if signed_xml:
            vals['signed_xml'] = signed_xml

        # Para RFCE: guardar también el XML ECF completo
        if signed_xml_ecf:
            vals['signed_xml_ecf'] = signed_xml_ecf

        # Para RFCE: guardar el código de seguridad del ECF (diferente al del RFCE)
        if ecf_security_code:
            vals['ecf_security_code'] = ecf_security_code
            _logger.info(f"[API Log] ===== GUARDANDO ecf_security_code: {ecf_security_code} =====")
        else:
            _logger.info(f"[API Log] ===== NO HAY ecf_security_code para guardar =====")

        _logger.info(f"[API Log] is_rfce={is_rfce}, ecf_security_code param={ecf_security_code}")
        _logger.info(f"[API Log] vals a escribir: {list(vals.keys())}")

        self.write(vals)

        # Si hay XML firmado, extraer automáticamente los datos para la URL de validación
        if signed_xml:
            self.extract_data_from_signed_xml()

        return self

    # ========================================================================
    # Acciones de Descarga
    # ========================================================================

    def action_download_request(self):
        """Descarga el payload enviado como JSON"""
        self.ensure_one()
        if not self.request_payload:
            raise UserError(_("No hay payload disponible."))

        filename = f"{self.encf or 'request'}_{self.id}.json"
        content = self.request_payload.encode('utf-8')
        b64_content = base64.b64encode(content).decode('utf-8')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': b64_content,
            'mimetype': 'application/json',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_download_response(self):
        """Descarga la respuesta completa"""
        self.ensure_one()
        if not self.response_body:
            raise UserError(_("No hay respuesta disponible."))

        # Determinar extensión según contenido
        ext = 'txt'
        mimetype = 'text/plain'
        body = self.response_body.strip()
        if body.startswith('{') or body.startswith('['):
            ext = 'json'
            mimetype = 'application/json'
        elif body.startswith('<?xml') or body.startswith('<'):
            ext = 'xml'
            mimetype = 'application/xml'

        filename = f"{self.encf or 'response'}_{self.id}.{ext}"
        content = self.response_body.encode('utf-8')
        b64_content = base64.b64encode(content).decode('utf-8')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': b64_content,
            'mimetype': mimetype,
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_download_incoming_xml(self):
        """Descarga el XML original recibido en el callback"""
        self.ensure_one()
        if not self.incoming_xml:
            raise UserError(_("No hay XML recibido disponible."))

        filename = f"{self.encf or 'incoming'}_{self.id}_recibido.xml"
        content = self.incoming_xml.encode('utf-8')
        b64_content = base64.b64encode(content).decode('utf-8')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': b64_content,
            'mimetype': 'application/xml',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_download_signed_xml(self):
        """Descarga el XML firmado (RFCE para consumo < 250k, ECF normal para otros)"""
        self.ensure_one()
        if not self.signed_xml:
            raise UserError(_("No hay XML firmado disponible."))

        # Determinar nombre según tipo
        suffix = "_RFCE" if self.is_rfce else "_firmado"
        filename = f"{self.encf or 'documento'}{suffix}.xml"
        content = self.signed_xml.encode('utf-8')
        b64_content = base64.b64encode(content).decode('utf-8')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': b64_content,
            'mimetype': 'application/xml',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_download_signed_xml_ecf(self):
        """Descarga el XML ECF completo firmado (solo disponible para RFCE)"""
        self.ensure_one()
        if not self.signed_xml_ecf:
            raise UserError(_("No hay XML ECF completo disponible. Solo disponible para documentos RFCE."))

        filename = f"{self.encf or 'documento'}_ECF_completo.xml"
        content = self.signed_xml_ecf.encode('utf-8')
        b64_content = base64.b64encode(content).decode('utf-8')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': b64_content,
            'mimetype': 'application/xml',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'new',
        }

    def action_view_detail(self):
        """Abre este log en vista de formulario"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ecf.api.log',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_origin(self):
        """Abre el documento/caso de origen"""
        self.ensure_one()
        if self.simulation_doc_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'ecf.simulation.document',
                'res_id': self.simulation_doc_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        elif self.test_case_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'ecf.test.case',
                'res_id': self.test_case_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        elif self.acecf_case_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'acecf.case',
                'res_id': self.acecf_case_id.id,
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            raise UserError(_("No hay registro de origen asociado."))

    # ========================================================================
    # Limpieza
    # ========================================================================

    @api.model
    def cleanup_old_logs(self, days=30):
        """Elimina logs antiguos (para llamar desde cron)"""
        from datetime import timedelta
        cutoff_date = fields.Datetime.now() - timedelta(days=days)
        old_logs = self.search([('create_date', '<', cutoff_date)])
        count = len(old_logs)
        old_logs.unlink()
        _logger.info(f"[API Log] Eliminados {count} logs anteriores a {cutoff_date}")
        return count
