# -*- coding: utf-8 -*-

import json
import logging
import requests
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EcfApiProvider(models.Model):
    """
    Modelo para gestionar múltiples proveedores de API para envío de e-CF.
    Permite configurar MSeller, API Local, u otras APIs personalizadas.
    """
    _name = "ecf.api.provider"
    _description = "Proveedor de API e-CF"
    _order = "sequence, name"

    name = fields.Char(string="Nombre", required=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
    is_default = fields.Boolean(
        string="Por Defecto",
        help="Usar este proveedor como predeterminado para envíos"
    )

    provider_type = fields.Selection([
        ('mseller', 'MSeller API'),
        ('local', 'API Local'),
        ('custom', 'API Personalizada'),
    ], string="Tipo de Proveedor", required=True, default='mseller')

    # ========================================================================
    # Configuración General
    # ========================================================================
    api_url = fields.Char(
        string="URL Base API",
        help="URL base del endpoint de la API (ej: /api/invoice/send)"
    )
    api_url_summary = fields.Char(
        string="URL Resumen con ECF (Consumo < 250k)",
        help="URL para facturas de consumo menores a 250,000 que devuelve ambos XMLs "
             "(ej: /api/invoice/send-summary-with-ecf). Recibe ECF completo y devuelve "
             "signedEcfXml y signedRfceXml."
    )
    api_url_acecf = fields.Char(
        string="URL ACECF (Aprobaciones Comerciales)",
        help="URL para enviar aprobaciones comerciales e-CF "
             "(ej: /api/invoice/acecf)"
    )
    environment = fields.Selection([
        ('test', 'Pruebas'),
        ('cert', 'Certificación'),
        ('prod', 'Producción'),
    ], string="Ambiente", default='cert')

    timeout = fields.Integer(
        string="Timeout (segundos)",
        default=60,
        help="Tiempo máximo de espera para respuesta"
    )

    # ========================================================================
    # Autenticación
    # ========================================================================
    auth_type = fields.Selection([
        ('none', 'Sin Autenticación'),
        ('bearer', 'Bearer Token'),
        ('api_key', 'API Key (Header)'),
        ('basic', 'Basic Auth'),
        ('mseller', 'MSeller (Email/Password + API Key)'),
    ], string="Tipo de Autenticación", default='none')

    auth_token = fields.Char(string="Token/API Key")
    auth_username = fields.Char(string="Usuario/Email")
    auth_password = fields.Char(string="Contraseña")
    api_key_header = fields.Char(
        string="Header API Key",
        default="x-api-key",
        help="Nombre del header para la API Key (ej: x-api-key, X-API-KEY)"
    )

    # ========================================================================
    # Configuración MSeller específica
    # ========================================================================
    mseller_env = fields.Selection([
        ('TesteCF', 'Pruebas (TesteCF)'),
        ('CerteCF', 'Certificación (CerteCF)'),
        ('eCF', 'Producción (eCF)'),
    ], string="Ambiente MSeller", default='TesteCF')

    # ========================================================================
    # Formato de Payload (para API Local/Custom)
    # ========================================================================
    payload_format = fields.Selection([
        ('ecf_direct', 'ECF Directo (solo el JSON del ECF)'),
        ('wrapped', 'Envuelto (invoiceData + metadata)'),
        ('custom', 'Personalizado'),
    ], string="Formato de Payload", default='ecf_direct',
       help="Cómo enviar el JSON del e-CF a la API")

    # Campos para formato wrapped
    wrapper_field = fields.Char(
        string="Campo Wrapper",
        default="invoiceData",
        help="Nombre del campo que contiene el ECF (ej: invoiceData)"
    )
    include_rnc = fields.Boolean(
        string="Incluir RNC",
        default=True,
        help="Agregar RNC del emisor al payload"
    )
    include_encf = fields.Boolean(
        string="Incluir eNCF",
        default=True,
        help="Agregar eNCF al payload"
    )
    include_environment = fields.Boolean(
        string="Incluir Ambiente",
        default=True,
        help="Agregar campo environment al payload"
    )

    # ========================================================================
    # Mapeo de Respuesta
    # ========================================================================
    response_track_id_field = fields.Char(
        string="Campo Track ID",
        default="trackId",
        help="Nombre del campo en la respuesta que contiene el Track ID"
    )
    response_status_field = fields.Char(
        string="Campo Estado",
        default="status",
        help="Nombre del campo en la respuesta que indica el estado"
    )
    response_message_field = fields.Char(
        string="Campo Mensaje",
        default="message",
        help="Nombre del campo en la respuesta con el mensaje"
    )

    # ========================================================================
    # Notas y Documentación
    # ========================================================================
    notes = fields.Text(
        string="Notas",
        help="Notas o documentación sobre este proveedor"
    )

    # ========================================================================
    # Constraints
    # ========================================================================

    @api.constrains('is_default')
    def _check_single_default(self):
        """Solo puede haber un proveedor por defecto"""
        for record in self:
            if record.is_default:
                others = self.search([
                    ('is_default', '=', True),
                    ('id', '!=', record.id)
                ])
                if others:
                    others.write({'is_default': False})

    # ========================================================================
    # Métodos de Envío
    # ========================================================================

    def _get_auth_headers(self, token=None):
        """Obtiene los headers de autenticación según el tipo configurado"""
        self.ensure_one()
        headers = {"Content-Type": "application/json"}

        # ===== DEBUG: Log detallado de configuración =====
        _logger.info("=" * 60)
        _logger.info("[AUTH DEBUG] ===== GENERANDO HEADERS DE AUTENTICACIÓN =====")
        _logger.info(f"[AUTH DEBUG] Provider ID: {self.id}")
        _logger.info(f"[AUTH DEBUG] Provider Name: {self.name}")
        _logger.info(f"[AUTH DEBUG] auth_type: '{self.auth_type}'")
        _logger.info(f"[AUTH DEBUG] auth_token existe: {bool(self.auth_token)}")
        _logger.info(f"[AUTH DEBUG] auth_token valor: '{self.auth_token[:10]}...' " if self.auth_token and len(self.auth_token) > 10 else f"[AUTH DEBUG] auth_token valor: '{self.auth_token}'")
        _logger.info(f"[AUTH DEBUG] api_key_header: '{self.api_key_header}'")
        _logger.info("=" * 60)

        if self.auth_type == 'bearer':
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
                _logger.info("[AUTH DEBUG] -> Agregando header Authorization: Bearer ****")
            else:
                _logger.warning("[AUTH DEBUG] -> auth_type='bearer' PERO auth_token ESTÁ VACÍO!")
        elif self.auth_type == 'api_key':
            if self.auth_token:
                header_name = self.api_key_header or "X-API-KEY"
                headers[header_name] = self.auth_token
                _logger.info(f"[AUTH DEBUG] -> Agregando header '{header_name}': '{self.auth_token}'")
            else:
                _logger.warning("[AUTH DEBUG] -> auth_type='api_key' PERO auth_token ESTÁ VACÍO!")
        elif self.auth_type == 'mseller' and token:
            headers["Authorization"] = f"Bearer {token}"
            if self.auth_token:
                headers["X-API-KEY"] = self.auth_token
            _logger.info("[AUTH DEBUG] -> Agregando headers MSeller (Bearer + X-API-KEY)")
        elif self.auth_type == 'none':
            _logger.warning("[AUTH DEBUG] -> auth_type='none' - NO SE ENVÍA AUTENTICACIÓN!")
        else:
            _logger.warning(f"[AUTH DEBUG] -> auth_type='{self.auth_type}' NO RECONOCIDO o token faltante")

        _logger.info(f"[AUTH DEBUG] Headers finales: {list(headers.keys())}")
        _logger.info("=" * 60)

        return headers

    def _mseller_login(self):
        """Login a MSeller API para obtener token"""
        self.ensure_one()
        if self.provider_type != 'mseller':
            raise UserError(_("Este método solo aplica para proveedores MSeller"))

        url = f"{self.api_url.rstrip('/')}/{self.mseller_env}/customer/authentication"
        payload = {
            "email": self.auth_username,
            "password": self.auth_password
        }

        try:
            r = requests.post(url, json=payload, timeout=self.timeout)
            data = r.json()

            if r.status_code >= 400:
                raise UserError(_(
                    "Login MSeller falló (%s):\n%s"
                ) % (r.status_code, json.dumps(data, ensure_ascii=False)))

            token = data.get("idToken") or data.get("token") or data.get("accessToken")
            if not token:
                raise UserError(_(
                    "Login MSeller OK pero no se encontró token:\n%s"
                ) % json.dumps(data, ensure_ascii=False))

            return token

        except requests.exceptions.RequestException as e:
            raise UserError(_("Error de conexión a MSeller:\n%s") % str(e))

    def _build_payload(self, ecf_json, rnc=None, encf=None):
        """Construye el payload según el formato configurado"""
        self.ensure_one()

        if self.payload_format == 'ecf_direct':
            return ecf_json

        elif self.payload_format == 'wrapped':
            # Formato: { "invoiceData": { "ECF": {...} }, "rnc": "...", "encf": "...", "environment": "..." }
            payload = {}

            # El ECF va dentro del wrapper
            wrapper_field = self.wrapper_field or 'invoiceData'
            payload[wrapper_field] = ecf_json

            # Agregar campos adicionales
            if self.include_rnc and rnc:
                payload['rnc'] = rnc
            if self.include_encf and encf:
                payload['encf'] = encf
            if self.include_environment:
                env_map = {
                    'test': 'test',
                    'cert': 'cert',
                    'prod': 'prod',
                }
                payload['environment'] = env_map.get(self.environment, 'cert')

            return payload

        else:  # custom
            # Por ahora igual que wrapped, se puede extender
            return ecf_json

    def _extract_response_data(self, response_data):
        """Extrae datos relevantes de la respuesta según configuración"""
        self.ensure_one()

        if not isinstance(response_data, dict):
            return None, None, None

        track_id = self._find_in_dict(response_data, self.response_track_id_field or 'trackId')
        status = self._find_in_dict(response_data, self.response_status_field or 'status')
        message = self._find_in_dict(response_data, self.response_message_field or 'message')

        return track_id, status, message

    def _find_in_dict(self, data, key):
        """Busca un valor en un diccionario, incluyendo variaciones de case"""
        if not isinstance(data, dict) or not key:
            return None

        # Búsqueda directa
        if key in data:
            return data[key]

        # Búsqueda case-insensitive
        key_lower = key.lower()
        for k, v in data.items():
            if k.lower() == key_lower:
                return v

        # Búsqueda en sub-diccionarios comunes
        for subkey in ['data', 'result', 'response', 'documento']:
            if subkey in data and isinstance(data[subkey], dict):
                result = self._find_in_dict(data[subkey], key)
                if result:
                    return result

        return None

    def _extract_signed_xml(self, response_data, raw_response):
        """
        Extrae el XML firmado de la respuesta de la API.
        Busca en varios lugares comunes donde podría estar el XML.
        """
        signed_xml = None

        # 1. Buscar en el diccionario de respuesta
        if isinstance(response_data, dict):
            # Campos comunes donde puede estar el XML
            xml_fields = [
                'xml', 'signedXml', 'signed_xml', 'xmlFirmado', 'xml_firmado',
                'xmlSigned', 'documento_xml', 'documentoXml', 'ecfXml', 'ecf_xml',
                'xmlDocument', 'xmlResponse', 'signedDocument', 'data'
            ]

            for field in xml_fields:
                val = self._find_in_dict(response_data, field)
                if val and isinstance(val, str):
                    # Verificar si es XML (empieza con <?xml o <ECF o <eCF)
                    val_stripped = val.strip()
                    if val_stripped.startswith('<?xml') or val_stripped.startswith('<ECF') or val_stripped.startswith('<eCF'):
                        signed_xml = val
                        break
                    # También verificar si es base64 encoded XML
                    if len(val) > 50 and not val.startswith('{'):
                        try:
                            import base64
                            decoded = base64.b64decode(val).decode('utf-8')
                            if decoded.strip().startswith('<?xml') or decoded.strip().startswith('<ECF'):
                                signed_xml = decoded
                                break
                        except Exception:
                            pass

        # 2. Si no encontramos en el dict, verificar si raw_response es directamente XML
        if not signed_xml and raw_response:
            raw_stripped = raw_response.strip()
            if raw_stripped.startswith('<?xml') or raw_stripped.startswith('<ECF') or raw_stripped.startswith('<eCF'):
                signed_xml = raw_response

        return signed_xml

    def _is_consumo_summary(self, ecf_json):
        """
        Determina si el documento es una factura de consumo (tipo 32)
        con monto menor a 250,000 que debe usar el endpoint de resumen.

        Returns:
            bool: True si debe usar endpoint send-summary
        """
        try:
            ecf = ecf_json.get('ECF', {})
            encabezado = ecf.get('Encabezado', {})
            id_doc = encabezado.get('IdDoc', {})
            # CORRECCIÓN: Totales está dentro de Encabezado, no directamente en ECF
            totales = encabezado.get('Totales', {})

            tipo_ecf = str(id_doc.get('TipoeCF', ''))
            monto_total = float(totales.get('MontoTotal', 0))

            _logger.info(f"[API Provider] _is_consumo_summary - TipoeCF: {tipo_ecf}, MontoTotal: {monto_total}")

            # Factura de Consumo Electrónica = tipo 32
            # Si monto < 250,000 usar endpoint de resumen
            if tipo_ecf == '32' and monto_total < 250000:
                _logger.info(f"[API Provider] Factura consumo tipo 32, monto {monto_total} < 250,000 -> usar send-summary")
                return True

            _logger.info(f"[API Provider] No es consumo summary (tipo={tipo_ecf}, monto={monto_total})")
            return False
        except Exception as e:
            _logger.warning(f"[API Provider] Error verificando si es consumo summary: {e}")
            return False

    def _convert_ecf_to_rfce(self, ecf_json):
        """
        Convierte un ECF (tipo 32 < 250k) a formato RFCE para send-summary.

        Campos ELIMINADOS en RFCE (no válidos):
        - IndicadorMontoGravado, NombreComercial, DireccionEmisor
        - TablaTelefonoEmisor, CorreoEmisor, CorreoComprador
        - DireccionComprador, MunicipioComprador, ProvinciaComprador
        - TelefonoAdicional, ITBIS1/2/3, DetallesItems, FechaHoraFirma

        Campos REQUERIDOS en RFCE:
        - MontoExento, MontoNoFacturable, MontoPeriodo, CodigoSeguridadeCF

        Formato:
        - TipoeCF y TipoPago como números (int)
        - Montos como números (float/int)
        - CodigoSeguridadeCF dentro de Encabezado (NO puede estar vacío)
        """
        import random
        import string as string_module

        def to_number(val):
            """Convierte string a número entero (la API RFCE requiere enteros, no floats)"""
            if val is None:
                return 0
            try:
                s = str(val).replace(',', '')
                # Convertir a float primero, luego redondear a entero
                # La API RFCE no acepta floats como 10100.0, requiere 10100
                f = float(s)
                return int(round(f))
            except:
                return 0

        def to_int(val):
            """Convierte a entero"""
            if val is None:
                return 0
            try:
                return int(str(val).replace(',', '').split('.')[0])
            except:
                return 0

        try:
            ecf = ecf_json.get('ECF', {})
            encabezado_orig = ecf.get('Encabezado', {})
            id_doc_orig = encabezado_orig.get('IdDoc', {})
            emisor_orig = encabezado_orig.get('Emisor', {})
            comprador_orig = encabezado_orig.get('Comprador', {})
            totales_orig = encabezado_orig.get('Totales', {})

            # ===== IdDoc =====
            # Solo: TipoeCF (int), eNCF (str), TipoIngresos (str), TipoPago (int)
            # Excluir: IndicadorMontoGravado, FechaVencimientoSecuencia
            id_doc_rfce = {}
            if 'TipoeCF' in id_doc_orig:
                id_doc_rfce['TipoeCF'] = to_int(id_doc_orig['TipoeCF'])
            if 'eNCF' in id_doc_orig:
                id_doc_rfce['eNCF'] = id_doc_orig['eNCF']
            if 'TipoIngresos' in id_doc_orig:
                id_doc_rfce['TipoIngresos'] = id_doc_orig['TipoIngresos']
            if 'TipoPago' in id_doc_orig:
                id_doc_rfce['TipoPago'] = to_int(id_doc_orig['TipoPago'])

            # ===== Emisor =====
            # Solo: RNCEmisor, RazonSocialEmisor, FechaEmision
            # Excluir: NombreComercial, DireccionEmisor, TablaTelefonoEmisor, CorreoEmisor
            emisor_rfce = {}
            for key in ['RNCEmisor', 'RazonSocialEmisor', 'FechaEmision']:
                if key in emisor_orig:
                    emisor_rfce[key] = emisor_orig[key]

            # ===== Comprador =====
            # Solo: RNCComprador, RazonSocialComprador
            # Excluir: CorreoComprador, DireccionComprador, MunicipioComprador,
            #          ProvinciaComprador, TelefonoAdicional
            comprador_rfce = {}
            if comprador_orig:
                for key in ['RNCComprador', 'RazonSocialComprador']:
                    if key in comprador_orig:
                        comprador_rfce[key] = comprador_orig[key]

            # ===== Totales =====
            # IMPORTANTE: El orden de los campos es crítico para la API
            # Orden correcto: MontoGravadoTotal, MontoGravadoI1-I3, MontoExento,
            #                 TotalITBIS, TotalITBIS1-3, MontoTotal, MontoNoFacturable, MontoPeriodo
            totales_rfce = {}

            # 1. MontoGravadoTotal y MontoGravadoI1-I3
            for key in ['MontoGravadoTotal', 'MontoGravadoI1', 'MontoGravadoI2', 'MontoGravadoI3']:
                if key in totales_orig:
                    totales_rfce[key] = to_number(totales_orig[key])

            # 2. MontoExento (REQUERIDO - va ANTES de TotalITBIS)
            totales_rfce['MontoExento'] = to_number(totales_orig.get('MontoExento', 0))

            # 3. TotalITBIS y TotalITBIS1-3
            for key in ['TotalITBIS', 'TotalITBIS1', 'TotalITBIS2', 'TotalITBIS3']:
                if key in totales_orig:
                    totales_rfce[key] = to_number(totales_orig[key])

            # 4. MontoTotal
            if 'MontoTotal' in totales_orig:
                totales_rfce['MontoTotal'] = to_number(totales_orig['MontoTotal'])

            # 5. MontoNoFacturable (REQUERIDO)
            totales_rfce['MontoNoFacturable'] = 0

            # 6. MontoPeriodo (REQUERIDO - igual a MontoTotal)
            totales_rfce['MontoPeriodo'] = totales_rfce.get('MontoTotal', 0)

            # ===== CodigoSeguridadeCF =====
            # REQUERIDO y NO puede estar vacío - generar si no existe
            codigo_seguridad = encabezado_orig.get('CodigoSeguridadeCF', '')
            if not codigo_seguridad:
                codigo_seguridad = ''.join(random.choices(
                    string_module.ascii_uppercase + string_module.digits, k=6
                ))

            # ===== Construir Encabezado RFCE =====
            encabezado_rfce = {
                'Version': encabezado_orig.get('Version', '1.0'),
                'IdDoc': id_doc_rfce,
                'Emisor': emisor_rfce,
            }

            if comprador_rfce:
                encabezado_rfce['Comprador'] = comprador_rfce

            encabezado_rfce['Totales'] = totales_rfce
            encabezado_rfce['CodigoSeguridadeCF'] = codigo_seguridad

            # ===== Construir RFCE final =====
            # SIN: DetallesItems, FechaHoraFirma
            rfce = {
                'RFCE': {
                    'Encabezado': encabezado_rfce
                }
            }

            _logger.info(f"[API Provider] ECF convertido a RFCE exitosamente")
            _logger.info(f"[API Provider] RFCE: {json.dumps(rfce, ensure_ascii=False)}")

            return rfce

        except Exception as e:
            _logger.error(f"[API Provider] Error convirtiendo ECF a RFCE: {e}")
            raise

    def send_ecf(self, ecf_json, rnc=None, encf=None, origin='other',
                 test_case_id=None, simulation_doc_id=None, acecf_case_id=None):
        """
        Envia un documento e-CF usando este proveedor y registra en log.

        Args:
            ecf_json: dict con el JSON del ECF (estructura {"ECF": {...}} o {"ACECF": {...}})
            rnc: RNC del emisor (opcional, para API local)
            encf: eNCF del documento (opcional, para API local)
            origin: origen del envio ('simulation', 'test_case', 'acecf_case', 'wizard', etc.)
            test_case_id: ID del caso de prueba (opcional)
            simulation_doc_id: ID del documento de simulacion (opcional)
            acecf_case_id: ID del caso ACECF (opcional)

        Returns:
            tuple: (success, response_data, track_id, error_message, raw_response, signed_xml)
            - success: bool indicando si fue exitoso
            - response_data: dict con datos parseados de la respuesta
            - track_id: ID de tracking
            - error_message: mensaje de error (o None si exitoso)
            - raw_response: respuesta completa sin procesar (str)
            - signed_xml: XML firmado si existe (str o None)
        """
        self.ensure_one()
        import time
        start_time = time.time()

        # Extraer RNC, eNCF y tipo del JSON si no se proporcionaron
        tipo_ecf = None
        if not rnc or not encf:
            try:
                encabezado = ecf_json.get('ECF', {}).get('Encabezado', {})
                if not rnc:
                    rnc = encabezado.get('Emisor', {}).get('RNCEmisor')
                id_doc = encabezado.get('IdDoc', {})
                if not encf:
                    encf = id_doc.get('eNCF')
                tipo_ecf = id_doc.get('TipoeCF')
            except Exception:
                pass

        # Determinar si usar endpoint de resumen para consumo < 250k
        use_summary_endpoint = self._is_consumo_summary(ecf_json)

        _logger.info(f"[API Provider] Enviando e-CF via {self.name} - RNC: {rnc}, eNCF: {encf}")

        # Crear log antes de enviar
        ApiLog = self.env['ecf.api.log']
        api_log = ApiLog.create_from_request(
            provider=self,
            origin=origin,
            test_case_id=test_case_id,
            simulation_doc_id=simulation_doc_id,
            acecf_case_id=acecf_case_id,
            request_payload=ecf_json,
            rnc=rnc,
            encf=encf,
            tipo_ecf=tipo_ecf
        )

        try:
            if self.provider_type == 'mseller':
                result = self._send_mseller(ecf_json, use_summary=use_summary_endpoint)
            elif self.provider_type == 'local':
                result = self._send_local(ecf_json, rnc, encf, use_summary=use_summary_endpoint)
            else:  # custom
                result = self._send_custom(ecf_json, rnc, encf, use_summary=use_summary_endpoint)

            # Calcular tiempo de respuesta
            response_time_ms = int((time.time() - start_time) * 1000)

            # Actualizar log con respuesta
            # El resultado puede tener 6, 7 u 8 elementos:
            # - 6: básico (success, response_data, track_id, error_msg, raw_response, signed_xml)
            # - 7: con signed_xml_ecf (RFCE antiguo)
            # - 8: con signed_xml_ecf y ecf_security_code (RFCE nuevo)
            signed_xml_ecf = None
            ecf_security_code = None

            if len(result) == 8:
                success, response_data, track_id, error_msg, raw_response, signed_xml, signed_xml_ecf, ecf_security_code = result
                _logger.info(f"[API Provider] ===== Resultado 8 elementos, ecf_security_code: {ecf_security_code} =====")
            elif len(result) == 7:
                success, response_data, track_id, error_msg, raw_response, signed_xml, signed_xml_ecf = result
                _logger.info(f"[API Provider] ===== Resultado 7 elementos, sin ecf_security_code =====")
            else:
                success, response_data, track_id, error_msg, raw_response, signed_xml = result
                _logger.info(f"[API Provider] ===== Resultado 6 elementos, sin ecf_security_code =====")

            _logger.info(f"[API Provider] Llamando update_with_response con ecf_security_code={ecf_security_code}")

            api_log.update_with_response(
                success=success,
                status_code=response_data.get('_status_code') if isinstance(response_data, dict) else None,
                response_body=raw_response,
                response_json=response_data,
                track_id=track_id,
                signed_xml=signed_xml,
                signed_xml_ecf=signed_xml_ecf,
                is_rfce=use_summary_endpoint,
                ecf_security_code=ecf_security_code,
                error_message=error_msg,
                response_time_ms=response_time_ms
            )

            # Devolver siempre 6 elementos para compatibilidad
            return success, response_data, track_id, error_msg, raw_response, signed_xml

        except Exception as e:
            _logger.error(f"[API Provider] Error al enviar: {str(e)}", exc_info=True)
            # Actualizar log con error
            response_time_ms = int((time.time() - start_time) * 1000)
            api_log.update_with_response(
                success=False,
                error_message=str(e),
                response_time_ms=response_time_ms
            )
            return False, None, None, str(e), None, None

    def _send_mseller(self, ecf_json, use_summary=False):
        """Envía documento a MSeller API"""
        self.ensure_one()

        # Login para obtener token
        token = self._mseller_login()

        # Construir URL y headers
        # MSeller podría tener diferentes endpoints para resumen vs normal
        url = f"{self.api_url.rstrip('/')}/{self.mseller_env}/documentos-ecf"
        if use_summary:
            _logger.info("[MSeller] Usando endpoint normal (MSeller maneja internamente el tipo de documento)")
        headers = self._get_auth_headers(token=token)

        # Enviar
        try:
            r = requests.post(url, headers=headers, json=ecf_json, timeout=self.timeout)
            raw_response = r.text

            try:
                response_data = r.json()
            except Exception:
                response_data = {"raw_response": raw_response}

            track_id, status, message = self._extract_response_data(response_data)

            # Buscar XML firmado en la respuesta
            signed_xml = self._extract_signed_xml(response_data, raw_response)

            # MSeller puede devolver HTTP 200 con un error de negocio en el body
            # (ej. rechazo de la DGII por estructura de XML invalida), no solo
            # errores HTTP. Hay que revisar el campo 'error' explicitamente.
            mseller_error = None
            if isinstance(response_data, dict):
                mseller_error = response_data.get('error') or response_data.get('mensaje')

            if 200 <= r.status_code < 300 and not mseller_error:
                return True, response_data, track_id, None, raw_response, signed_xml
            else:
                error_msg = mseller_error or message or f"HTTP {r.status_code}"
                return False, response_data, track_id, error_msg, raw_response, signed_xml

        except requests.exceptions.RequestException as e:
            return False, None, None, str(e), None, None

    def _send_local(self, ecf_json, rnc, encf, use_summary=False):
        """Envía documento a API Local"""
        self.ensure_one()

        if not self.api_url:
            return False, None, None, "URL de API no configurada", None, None

        # Debug: mostrar valores de URLs configuradas
        _logger.info(f"[API Local] ========== DEBUG URL SUMMARY ==========")
        _logger.info(f"[API Local] api_url configurada: '{self.api_url}'")
        _logger.info(f"[API Local] api_url_summary RAW: '{self.api_url_summary}'")
        _logger.info(f"[API Local] api_url_summary TYPE: {type(self.api_url_summary)}")
        _logger.info(f"[API Local] api_url_summary BOOL: {bool(self.api_url_summary)}")
        _logger.info(f"[API Local] api_url_summary REPR: {repr(self.api_url_summary)}")
        _logger.info(f"[API Local] use_summary solicitado: {use_summary}")
        _logger.info(f"[API Local] Provider ID: {self.id}, Name: {self.name}")
        _logger.info(f"[API Local] ==========================================")

        # Determinar URL a usar
        # Para consumo < 250k: usar endpoint combinado que devuelve ambos XMLs
        summary_url = (self.api_url_summary or '').strip()
        _logger.info(f"[API Local] summary_url después de strip: '{summary_url}' (len={len(summary_url)})")
        if use_summary and summary_url:
            url = summary_url
            _logger.info(f"[API Local] Usando endpoint combinado RFCE+ECF para consumo < 250k: {url}")
        else:
            url = self.api_url
            if use_summary and not summary_url:
                _logger.warning("[API Local] Se requiere endpoint de resumen pero no está configurado, usando URL normal")

        # Siempre enviar ECF completo - la API se encarga de la conversión RFCE
        doc_to_send = ecf_json

        # Construir payload según formato
        payload = self._build_payload(doc_to_send, rnc, encf)

        # Headers
        headers = self._get_auth_headers()

        _logger.info(f"[API Local] ========== INICIO ENVÍO ==========")
        _logger.info(f"[API Local] URL: {url}")
        _logger.info(f"[API Local] Tipo: {'RESUMEN (consumo < 250k)' if use_summary else 'NORMAL'}")
        _logger.info(f"[API Local] Headers: {headers}")
        _logger.info(f"[API Local] Payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'not dict'}")
        _logger.info(f"[API Local] RNC: {rnc}, eNCF: {encf}")
        _logger.info(f"[API Local] Payload completo: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            raw_response = r.text

            _logger.info(f"[API Local] Response status: {r.status_code}")
            _logger.info(f"[API Local] Response body (primeros 1000): {raw_response[:1000]}")

            try:
                response_data = r.json()
            except Exception as json_err:
                _logger.warning(f"[API Local] No se pudo parsear JSON: {json_err}")
                response_data = {"raw_response": raw_response}

            track_id, status, message = self._extract_response_data(response_data)
            _logger.info(f"[API Local] Extracted - trackId: {track_id}, status: {status}, message: {message}")

            if 200 <= r.status_code < 300:
                _logger.info(f"[API Local] ========== ENVÍO EXITOSO ==========")

                # Extraer XMLs de la respuesta
                signed_xml = None
                signed_xml_ecf = None

                # Variable para guardar el código de seguridad ECF (para RFCE)
                ecf_security_code = None

                if use_summary and isinstance(response_data, dict):
                    # Endpoint combinado devuelve: { success: true, data: { signedRfceXml, signedEcfXml, ecfSecurityCode, rfceSecurityCode, ... } }
                    # Los XMLs pueden estar en response_data directamente o dentro de response_data.data
                    data_obj = response_data.get('data', response_data)

                    _logger.info(f"[API Local] ===== ANALIZANDO RESPUESTA RFCE =====")
                    _logger.info(f"[API Local] response_data keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'not dict'}")
                    _logger.info(f"[API Local] data_obj keys: {list(data_obj.keys()) if isinstance(data_obj, dict) else 'not dict'}")

                    signed_xml = data_obj.get('signedRfceXml') or data_obj.get('signedXml')
                    signed_xml_ecf = data_obj.get('signedEcfXml')

                    # IMPORTANTE: Para RFCE, el código de seguridad para la URL de validación
                    # es ecfSecurityCode (del ECF completo), NO rfceSecurityCode (del RFCE)
                    ecf_security_code = data_obj.get('ecfSecurityCode')
                    rfce_security_code = data_obj.get('rfceSecurityCode')

                    _logger.info(f"[API Local] ===== SECURITY CODES =====")
                    _logger.info(f"[API Local] ecfSecurityCode (CORRECTO para URL): {ecf_security_code}")
                    _logger.info(f"[API Local] rfceSecurityCode (NO usar en URL): {rfce_security_code}")

                    if signed_xml:
                        _logger.info(f"[API Local] XML RFCE firmado encontrado ({len(signed_xml)} bytes)")
                    if signed_xml_ecf:
                        _logger.info(f"[API Local] XML ECF firmado encontrado ({len(signed_xml_ecf)} bytes)")

                    # Log adicional para debug
                    if not signed_xml and not signed_xml_ecf:
                        _logger.warning(f"[API Local] No se encontraron XMLs en la respuesta. Keys en data: {list(data_obj.keys()) if isinstance(data_obj, dict) else 'no dict'}")
                else:
                    # Endpoint normal: buscar XML firmado en campos habituales
                    signed_xml = self._extract_signed_xml(response_data, raw_response)
                    if signed_xml:
                        _logger.info(f"[API Local] XML firmado encontrado ({len(signed_xml)} bytes)")

                # Devolver 8 elementos si tenemos signed_xml_ecf (RFCE) con ecf_security_code
                if signed_xml_ecf:
                    return True, response_data, track_id, None, raw_response, signed_xml, signed_xml_ecf, ecf_security_code
                return True, response_data, track_id, None, raw_response, signed_xml
            else:
                error_msg = message or f"HTTP {r.status_code}: {raw_response[:200]}"
                _logger.warning(f"[API Local] ========== ENVÍO FALLIDO: {error_msg} ==========")
                # Buscar XML en respuesta de error también
                signed_xml = self._extract_signed_xml(response_data, raw_response)
                return False, response_data, track_id, error_msg, raw_response, signed_xml

        except requests.exceptions.Timeout:
            _logger.error(f"[API Local] TIMEOUT después de {self.timeout} segundos")
            return False, None, None, f"Timeout después de {self.timeout} segundos", None, None
        except requests.exceptions.ConnectionError as e:
            _logger.error(f"[API Local] ERROR DE CONEXIÓN: {str(e)}")
            return False, None, None, f"Error de conexión: {str(e)}", None, None
        except requests.exceptions.RequestException as e:
            _logger.error(f"[API Local] ERROR REQUEST: {str(e)}")
            return False, None, None, str(e), None, None

    def _send_custom(self, ecf_json, rnc, encf, use_summary=False):
        """Envía documento a API personalizada (mismo que local por ahora)"""
        return self._send_local(ecf_json, rnc, encf, use_summary=use_summary)

    def send_acecf(self, acecf_json, origin='acecf_case', acecf_case_id=None, environment='cert'):
        """
        Envia una Aprobacion Comercial e-CF (ACECF) a la API.

        Usa la misma configuracion del proveedor (URL base, API key, etc.)
        y envia al endpoint /api/invoice/approval con formato anidado.

        Args:
            acecf_json: dict con estructura ACECF o campos planos
            origin: origen del envio
            acecf_case_id: ID del caso ACECF
            environment: ambiente de DGII ('test', 'cert', 'prod')

        Returns:
            tuple: (success, response_data, track_id, error_message, raw_response, signed_xml)
        """
        self.ensure_one()
        import time
        start_time = time.time()

        # Extraer datos del JSON ACECF
        if 'ACECF' in acecf_json:
            detalle = acecf_json.get('ACECF', {}).get('DetalleAprobacionComercial', {})
        else:
            detalle = acecf_json

        # Construir estructura ACECF completa para approvalData
        acecf_detalle = {
            'Version': detalle.get('Version') or '1.0',
            'RNCEmisor': detalle.get('RNCEmisor'),
            'eNCF': detalle.get('eNCF'),
            'FechaEmision': detalle.get('FechaEmision'),
            'MontoTotal': str(detalle.get('MontoTotal')) if detalle.get('MontoTotal') else None,
            'RNCComprador': detalle.get('RNCComprador'),
            'Estado': int(detalle.get('Estado')) if detalle.get('Estado') else 1,
        }

        # Campos opcionales
        if detalle.get('FechaHoraAprobacionComercial'):
            acecf_detalle['FechaHoraAprobacionComercial'] = detalle.get('FechaHoraAprobacionComercial')
        if detalle.get('DetalleMotivoRechazo'):
            acecf_detalle['DetalleMotivoRechazo'] = detalle.get('DetalleMotivoRechazo')

        # Limpiar valores None del detalle
        acecf_detalle = {k: v for k, v in acecf_detalle.items() if v is not None}

        rnc_comprador = acecf_detalle.get('RNCComprador')
        encf = acecf_detalle.get('eNCF')

        # Construir fileName: RNCComprador + eNCF + .xml
        file_name = f"{rnc_comprador}{encf}.xml" if rnc_comprador and encf else None

        # Construir payload en formato anidado que espera /api/invoice/approval
        payload = {
            'approvalData': {
                'ACECF': {
                    'DetalleAprobacionComercial': acecf_detalle
                }
            },
            'fileName': file_name,
            'rnc': rnc_comprador,
            'environment': environment
        }

        rnc = acecf_detalle.get('RNCEmisor')

        # Construir URL para /api/invoice/approval
        # Siempre usa el endpoint /api/invoice/approval (ignora api_url_acecf si tiene el endpoint antiguo)
        if self.api_url:
            base_url = self.api_url.strip().rstrip('/')
            # Buscar /api/ en la URL para construir el endpoint correcto
            if '/api/' in base_url:
                api_base = base_url.split('/api/')[0]
                acecf_url = f"{api_base}/api/invoice/approval"
            else:
                # Fallback: agregar endpoint
                acecf_url = f"{base_url}/api/invoice/approval"
        elif self.api_url_acecf:
            # Si solo tiene api_url_acecf, reemplazar /acecf por /approval
            acecf_url = self.api_url_acecf.strip().replace('/acecf', '/approval')
        else:
            return False, None, None, "URL de API no configurada en el proveedor", None, None

        _logger.info(f"[ACECF] Enviando a {acecf_url} - eNCF: {encf}")

        # Obtener headers de autenticacion del proveedor
        headers = self._get_auth_headers()
        headers['Content-Type'] = 'application/json'

        # Crear log antes de enviar
        ApiLog = self.env['ecf.api.log']
        api_log = ApiLog.create_from_request(
            provider=self,
            origin=origin,
            acecf_case_id=acecf_case_id,
            request_url=acecf_url,
            request_payload=payload,
            request_headers=headers,
            rnc=rnc,
            encf=encf,
            tipo_ecf='ACECF'
        )

        try:
            _logger.info(f"[ACECF] Headers: {headers}")
            _logger.info(f"[ACECF] Payload: {json.dumps(payload, ensure_ascii=False)}")

            r = requests.post(acecf_url, headers=headers, json=payload, timeout=self.timeout)
            raw_response = r.text
            response_time_ms = int((time.time() - start_time) * 1000)

            _logger.info(f"[ACECF] Status: {r.status_code}")
            _logger.info(f"[ACECF] Response: {raw_response[:500] if raw_response else 'empty'}")

            try:
                response_data = r.json()
            except Exception:
                response_data = {"raw_response": raw_response}

            # Determinar exito
            success = False
            track_id = None
            error_msg = None

            if isinstance(response_data, dict):
                success = response_data.get('success', False)
                track_id = response_data.get('trackId') or response_data.get('track_id')
                if not success:
                    error_msg = response_data.get('error') or response_data.get('message')

            if 200 <= r.status_code < 300 and success:
                api_log.update_with_response(
                    success=True,
                    status_code=r.status_code,
                    response_body=raw_response,
                    response_json=response_data,
                    track_id=track_id,
                    response_time_ms=response_time_ms
                )
                return True, response_data, track_id, None, raw_response, None
            else:
                error_msg = error_msg or f"HTTP {r.status_code}"
                api_log.update_with_response(
                    success=False,
                    status_code=r.status_code,
                    response_body=raw_response,
                    response_json=response_data,
                    error_message=error_msg,
                    response_time_ms=response_time_ms
                )
                return False, response_data, track_id, error_msg, raw_response, None

        except requests.exceptions.Timeout:
            response_time_ms = int((time.time() - start_time) * 1000)
            error_msg = f"Timeout despues de {self.timeout} segundos"
            api_log.update_with_response(success=False, error_message=error_msg, response_time_ms=response_time_ms)
            return False, None, None, error_msg, None, None
        except requests.exceptions.RequestException as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            api_log.update_with_response(success=False, error_message=error_msg, response_time_ms=response_time_ms)
            return False, None, None, error_msg, None, None

    # ========================================================================
    # Acciones
    # ========================================================================

    def action_test_connection(self):
        """Prueba la conexión con el proveedor - ahora con autenticación real"""
        self.ensure_one()

        _logger.info("=" * 70)
        _logger.info("[TEST CONNECTION] ===== INICIANDO PRUEBA DE CONEXIÓN =====")
        _logger.info(f"[TEST CONNECTION] Provider: {self.name} (ID: {self.id})")
        _logger.info(f"[TEST CONNECTION] URL: {self.api_url}")
        _logger.info(f"[TEST CONNECTION] Provider Type: {self.provider_type}")
        _logger.info(f"[TEST CONNECTION] Auth Type: {self.auth_type}")
        _logger.info(f"[TEST CONNECTION] API Key Header Config: {self.api_key_header}")
        _logger.info(f"[TEST CONNECTION] Auth Token Exists: {bool(self.auth_token)}")
        if self.auth_token:
            _logger.info(f"[TEST CONNECTION] Auth Token Value: '{self.auth_token}'")
        else:
            _logger.warning("[TEST CONNECTION] *** AUTH TOKEN ESTÁ VACÍO ***")
        _logger.info("=" * 70)

        try:
            if self.provider_type == 'mseller':
                token = self._mseller_login()
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Conexión Exitosa'),
                        'message': _('Login a MSeller exitoso. Token obtenido.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                # Para API Local/Custom: hacer POST real con payload mínimo
                if not self.api_url:
                    raise UserError(_("Configure la URL de la API primero"))

                # Obtener headers con autenticación
                headers = self._get_auth_headers()

                _logger.info(f"[TEST CONNECTION] Headers a enviar: {headers}")

                # Payload mínimo para probar autenticación
                test_payload = {
                    "invoiceData": {"test": True},
                    "rnc": "000000000",
                    "encf": "E310000000000"
                }

                _logger.info(f"[TEST CONNECTION] Enviando POST a: {self.api_url}")
                _logger.info(f"[TEST CONNECTION] Payload: {json.dumps(test_payload)}")

                r = requests.post(
                    self.api_url,
                    headers=headers,
                    json=test_payload,
                    timeout=self.timeout or 30
                )

                _logger.info(f"[TEST CONNECTION] Response Status: {r.status_code}")
                _logger.info(f"[TEST CONNECTION] Response Body: {r.text[:500]}")
                _logger.info("=" * 70)

                # Analizar respuesta
                if r.status_code == 401:
                    # Error de autenticación
                    error_detail = f"HTTP 401 - Autenticación fallida\n\n"
                    error_detail += f"Headers enviados:\n"
                    for k, v in headers.items():
                        if 'key' in k.lower() or 'auth' in k.lower():
                            error_detail += f"  {k}: {v}\n"
                        else:
                            error_detail += f"  {k}: {v}\n"
                    error_detail += f"\nRespuesta API:\n{r.text[:300]}"
                    raise UserError(_(error_detail))

                elif r.status_code == 400:
                    # Validación fallida pero autenticación OK
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Autenticación Exitosa'),
                            'message': _('La API Key es válida. (Error 400 es esperado con datos de prueba)'),
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                elif r.status_code == 500:
                    # Error interno pero autenticación OK
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Autenticación Exitosa'),
                            'message': _('La API Key es válida. (Error 500: %s)') % r.text[:100],
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Conexión OK'),
                            'message': _('API respondió con status %s') % r.status_code,
                            'type': 'success',
                            'sticky': False,
                        }
                    }

        except requests.exceptions.RequestException as e:
            _logger.error(f"[TEST CONNECTION] Error de conexión: {str(e)}")
            raise UserError(_("Error de conexión:\n%s") % str(e))
        except UserError:
            raise
        except Exception as e:
            _logger.error(f"[TEST CONNECTION] Error inesperado: {str(e)}")
            raise UserError(_("Error inesperado:\n%s") % str(e))

    def action_set_as_default(self):
        """Establece este proveedor como predeterminado"""
        self.ensure_one()
        self.search([('is_default', '=', True)]).write({'is_default': False})
        self.is_default = True
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Proveedor Actualizado'),
                'message': _('%s es ahora el proveedor por defecto') % self.name,
                'type': 'success',
                'sticky': False,
            }
        }

    @api.model
    def get_default_provider(self):
        """Obtiene el proveedor por defecto"""
        # Listar todos los proveedores para debug
        all_providers = self.search([])
        _logger.info(f"[API Provider] Todos los proveedores: {[(p.id, p.name, p.active, p.is_default) for p in all_providers]}")

        provider = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        _logger.info(f"[API Provider] Proveedor por defecto (is_default=True, active=True): {provider.name if provider else 'NINGUNO'}")

        if not provider:
            provider = self.search([('active', '=', True)], limit=1)
            _logger.info(f"[API Provider] Proveedor activo (fallback): {provider.name if provider else 'NINGUNO'}")

        return provider
