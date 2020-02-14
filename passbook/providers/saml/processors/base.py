"""Basic SAML Processor"""
from typing import TYPE_CHECKING, Dict, List, Union

from defusedxml import ElementTree
from django.http import HttpRequest
from structlog import get_logger

from passbook.providers.saml.exceptions import CannotHandleAssertion
from passbook.providers.saml.utils import get_random_id
from passbook.providers.saml.utils.encoding import decode_base64_and_inflate, nice64
from passbook.providers.saml.utils.time import get_time_string, timedelta_from_string
from passbook.providers.saml.utils.xml_render import get_assertion_xml, get_response_xml

if TYPE_CHECKING:
    from passbook.providers.saml.models import SAMLProvider

# pylint: disable=too-many-instance-attributes
class Processor:
    """Base SAML 2.0 AuthnRequest to Response Processor.
    Sub-classes should provide Service Provider-specific functionality."""

    is_idp_initiated = False

    _remote: "SAMLProvider"
    _http_request: HttpRequest

    _assertion_xml: str
    _response_xml: str
    _saml_response: str

    _relay_state: str
    _saml_request: str

    _assertion_params: Dict[str, Union[str, List[Dict[str, str]]]]
    _request_params: Dict[str, str]
    _system_params: Dict[str, str]
    _response_params: Dict[str, str]

    @property
    def subject_format(self) -> str:
        """Get subject Format"""
        return "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"

    def __init__(self, remote: "SAMLProvider"):
        self.name = remote.name
        self._remote = remote
        self._logger = get_logger()
        self._system_params = {
            "ISSUER": self._remote.issuer,
        }

    def _build_assertion(self):
        """Builds _assertion_params."""
        self._assertion_params = {
            "ASSERTION_ID": get_random_id(),
            "ASSERTION_SIGNATURE": "",  # it's unsigned
            "AUDIENCE": self._remote.audience,
            "AUTH_INSTANT": get_time_string(),
            "ISSUE_INSTANT": get_time_string(),
            "NOT_BEFORE": get_time_string(
                timedelta_from_string(self._remote.assertion_valid_not_before)
            ),
            "NOT_ON_OR_AFTER": get_time_string(
                timedelta_from_string(self._remote.assertion_valid_not_on_or_after)
            ),
            "SESSION_INDEX": self._http_request.session.session_key,
            "SESSION_NOT_ON_OR_AFTER": get_time_string(
                timedelta_from_string(self._remote.session_valid_not_on_or_after)
            ),
            "SP_NAME_QUALIFIER": self._remote.audience,
            "SUBJECT": self._http_request.user.email,
            "SUBJECT_FORMAT": self.subject_format,
        }
        self._assertion_params.update(self._system_params)
        self._assertion_params.update(self._request_params)

    def _build_response(self):
        """Builds _response_params."""
        self._response_params = {
            "ASSERTION": self._assertion_xml,
            "ISSUE_INSTANT": get_time_string(),
            "RESPONSE_ID": get_random_id(),
            "RESPONSE_SIGNATURE": "",  # initially unsigned
        }
        self._response_params.update(self._system_params)
        self._response_params.update(self._request_params)

    def _encode_response(self):
        """Encodes _response_xml to _encoded_xml."""
        self._saml_response = nice64(str.encode(self._response_xml))

    def _extract_saml_request(self):
        """Retrieves the _saml_request AuthnRequest from the _http_request."""
        self._saml_request = self._http_request.session["SAMLRequest"]
        self._relay_state = self._http_request.session["RelayState"]

    def _format_assertion(self):
        """Formats _assertion_params as _assertion_xml."""
        # https://commons.lbl.gov/display/IDMgmt/Attribute+Definitions
        self._assertion_params["ATTRIBUTES"] = [
            {
                "FriendlyName": "eduPersonPrincipalName",
                "Name": "urn:oid:1.3.6.1.4.1.5923.1.1.1.6",
                "Value": self._http_request.user.email,
            },
            {
                "FriendlyName": "cn",
                "Name": "urn:oid:2.5.4.3",
                "Value": self._http_request.user.name,
            },
            {
                "FriendlyName": "mail",
                "Name": "urn:oid:0.9.2342.19200300.100.1.3",
                "Value": self._http_request.user.email,
            },
            {
                "FriendlyName": "displayName",
                "Name": "urn:oid:2.16.840.1.113730.3.1.241",
                "Value": self._http_request.user.username,
            },
            {
                "FriendlyName": "uid",
                "Name": "urn:oid:0.9.2342.19200300.100.1.1",
                "Value": self._http_request.user.pk,
            },
        ]
        from passbook.providers.saml.models import SAMLPropertyMapping

        for mapping in self._remote.property_mappings.all().select_subclasses():
            if isinstance(mapping, SAMLPropertyMapping):
                mapping_payload = {
                    "Name": mapping.saml_name,
                    "ValueArray": [],
                    "FriendlyName": mapping.friendly_name,
                }
                for value in mapping.values:
                    mapping_payload["ValueArray"].append(
                        value.format(
                            user=self._http_request.user, request=self._http_request
                        )
                    )
                self._assertion_params["ATTRIBUTES"].append(mapping_payload)
        self._assertion_xml = get_assertion_xml(
            "saml/xml/assertions/generic.xml", self._assertion_params, signed=True
        )

    def _format_response(self):
        """Formats _response_params as _response_xml."""
        assertion_id = self._assertion_params["ASSERTION_ID"]
        self._response_xml = get_response_xml(
            self._response_params, saml_provider=self._remote, assertion_id=assertion_id
        )

    def _get_django_response_params(self) -> Dict[str, str]:
        """Returns a dictionary of parameters for the response template."""
        return {
            "acs_url": self._request_params["ACS_URL"],
            "saml_response": self._saml_response,
            "relay_state": self._relay_state,
            "autosubmit": self._remote.application.skip_authorization,
        }

    def _decode_and_parse_request(self):
        """Parses various parameters from _request_xml into _request_params."""
        decoded_xml = decode_base64_and_inflate(self._saml_request).decode("utf-8")

        root = ElementTree.fromstring(decoded_xml)

        params = {}
        params["ACS_URL"] = root.attrib.get(
            "AssertionConsumerServiceURL", self._remote.acs_url
        )
        params["REQUEST_ID"] = root.attrib["ID"]
        params["DESTINATION"] = root.attrib.get("Destination", "")
        params["PROVIDER_NAME"] = root.attrib.get("ProviderName", "")
        self._request_params = params

    def _validate_request(self):
        """
        Validates the SAML request against the SP configuration of this
        processor. Sub-classes should override this and raise a
        `CannotHandleAssertion` exception if the validation fails.

        Raises:
            CannotHandleAssertion: if the ACS URL specified in the SAML request
                doesn't match the one specified in the processor config.
        """
        request_acs_url = self._request_params["ACS_URL"]

        if self._remote.acs_url != request_acs_url:
            msg = "couldn't find ACS url '{}' in SAML2IDP_REMOTES " "setting.".format(
                request_acs_url
            )
            self._logger.info(msg)
            raise CannotHandleAssertion(msg)

    def _validate_user(self):
        """Validates the User. Sub-classes should override this and
        throw an CannotHandleAssertion Exception if the validation does not succeed."""

    def can_handle(self, request: HttpRequest) -> bool:
        """Returns true if this processor can handle this request."""
        self._http_request = request
        # Read the request.
        try:
            self._extract_saml_request()
        except Exception as exc:
            raise CannotHandleAssertion(
                f"can't find SAML request in user session: {exc}"
            ) from exc

        try:
            self._decode_and_parse_request()
        except Exception as exc:
            raise CannotHandleAssertion(f"can't parse SAML request: {exc}") from exc

        self._validate_request()
        return True

    def generate_response(self) -> Dict[str, str]:
        """Processes request and returns template variables suitable for a response."""
        # Build the assertion and response.
        # Only call can_handle if SP initiated Request, otherwise we have no Request
        if not self.is_idp_initiated:
            self.can_handle(self._http_request)

        self._validate_user()
        self._build_assertion()
        self._format_assertion()
        self._build_response()
        self._format_response()
        self._encode_response()

        # Return proper template params.
        return self._get_django_response_params()

    def init_deep_link(self, request: HttpRequest, url: str):
        """Initialize this Processor to make an IdP-initiated call to the SP's
        deep-linked URL."""
        self._http_request = request
        acs_url = self._remote.acs_url
        # NOTE: The following request params are made up. Some are blank,
        # because they comes over in the AuthnRequest, but we don't have an
        # AuthnRequest in this case:
        # - Destination: Should be this IdP's SSO endpoint URL. Not used in the response?
        # - ProviderName: According to the spec, this is optional.
        self._request_params = {
            "ACS_URL": acs_url,
            "DESTINATION": "",
            "PROVIDER_NAME": "",
        }
        self._relay_state = url
