# =============================================================================
#  VENDORED CODE — SideInstaller by FrizzleM   (NOT MIT)
# =============================================================================
#  Source   : https://github.com/FrizzleM/SideInstaller  ->  ish/sideinstaller.py
#  Copyright: (c) 2026 FrizzleM
#  Licence  : see LICENSE.md in this folder. Non-commercial; source-form
#             redistribution only; attribution required; changes must be noted.
#             This file is NOT covered by BariSign's MIT licence.
#
#  Taken: the developer-portal client (developerservices2) — teams, device
#         registration, development certificates, App IDs and provisioning
#         profiles.
#
#  CHANGES BY BARISIGN (required by the licence to be stated):
#    * every `openssl` call was replaced with `cryptography`: RSA key generation,
#      modulus comparison, DER->PEM conversion, and CSR creation. a-Shell has no
#      openssl binary, and this keeps the whole stage pure Python;
#    * the host/stdlib imports were reduced to what this file actually uses.
# =============================================================================

from __future__ import annotations

import hashlib
import os
import plistlib
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from signin import (DEV_CLIENT_ID, DEV_HOST, DEV_PROTOCOL, Fail, MACHINE_NAME,
                    STORAGE, ask, confirm, die, info, ok, request, step, warn)


# --- BariSign helpers replacing the openssl binary ---------------------------

def _load_key(path):
    with open(path, "rb") as handle:
        return serialization.load_pem_private_key(handle.read(), password=None)


def _load_cert(der_path):
    with open(der_path, "rb") as handle:
        return x509.load_der_x509_certificate(handle.read())


def _hex(modulus):
    data = modulus.to_bytes((modulus.bit_length() + 7) // 8, "big")
    return ":".join("%02X" % byte for byte in data)


def _der_to_pem(der_path, pem_path):
    cert = _load_cert(der_path)
    with open(pem_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))


def _make_csr(key):
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([
               x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
               x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "STATE"),
               x509.NameAttribute(NameOID.LOCALITY_NAME, "LOCAL"),
               x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ORGNIZATION"),
               x509.NameAttribute(NameOID.COMMON_NAME, "CN"),
           ]))
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM)


def dev_url(endpoint, device_type="ios/"):
    return "https://%s/services/%s/%s%s.action?clientId=%s" % (
        DEV_HOST, DEV_PROTOCOL, device_type, endpoint, DEV_CLIENT_ID
    )


class DeveloperSession:
    def __init__(self, account, token, adsid):
        self.account = account
        self.gs = account.gs
        self.anisette = account.anisette
        self.token = token
        self.adsid = adsid

    def headers(self):
        headers = self.gs.base_headers()
        headers.update(self.anisette.headers())
        headers["X-Apple-GS-Token"] = self.token
        headers["X-Apple-I-Identity-Id"] = self.adsid
        return headers

    def call(self, endpoint, body=None, key=None, device_type="ios/"):
        payload = {
            "clientId": DEV_CLIENT_ID,
            "protocolVersion": DEV_PROTOCOL,
            "requestId": str(uuid.uuid4()).upper(),
            "userLocale": ["en_US"],
        }
        payload.update(body or {})

        url = dev_url(endpoint, device_type)
        status, _, raw = request(
            "POST", url, headers=self.headers(),
            body=plistlib.dumps(payload, fmt=plistlib.FMT_XML),
        )
        if status != 200:
            raise Fail("Developer portal returned HTTP %d for %s." % (status, endpoint))

        response = plistlib.loads(raw)
        code = response.get("resultCode", 0)
        if code:
            message = response.get("userString") or response.get("resultString") or "no message"
            raise PortalError(int(code), message)
        if key is not None:
            if key not in response:
                raise Fail("Developer portal reply for %s had no '%s'." % (endpoint, key))
            return response[key]
        return response


class PortalError(Fail):
    def __init__(self, code, message):
        super().__init__("Apple developer portal error %d: %s" % (code, message))
        self.code = code
        self.message = message


# -- teams --------------------------------------------------------------------

def pick_team(session):
    teams = session.call("listTeams", key="teams", device_type="")
    if not teams:
        raise Fail("This Apple account has no developer team.\n"
                   "    Open https://developer.apple.com in Safari, sign in once, and accept the\n"
                   "    free developer agreement, then run this again.")
    if len(teams) == 1:
        return teams[0]

    info("This account is on more than one team:")
    for index, team in enumerate(teams, 1):
        info("  %d) %s (%s, %s)" % (index, team.get("name", "?"), team["teamId"],
                                    team.get("type", "?")))
    while True:
        choice = ask("Which team should sign the app?", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(teams):
            return teams[int(choice) - 1]


# -- device -------------------------------------------------------------------

def _same_udid(one, other):
    """Apple echoes the UDID with its own casing; compare the hex only."""
    strip = lambda value: "".join(ch for ch in (value or "").lower() if ch in "0123456789abcdef")
    return strip(one) == strip(other)


def list_devices(session, team):
    """Every device registered on this team (not the same as iCloud devices)."""
    return session.call("listDevices", {"teamId": team["teamId"]}, key="devices") or []


def ensure_device(session, team, udid, name):
    devices = session.call("listDevices", {"teamId": team["teamId"]}, key="devices")
    if any(_same_udid(d.get("deviceNumber"), udid) for d in devices):
        return
    try:
        session.call("addDevice", {
            "teamId": team["teamId"], "name": name, "deviceNumber": udid,
        }, key="device")
    except PortalError as exc:
        if exc.code == 35:  # already registered on this team
            return
        raise


# -- signing identity ---------------------------------------------------------

def private_key_path(email):
    digest = hashlib.sha256(email.encode()).hexdigest()
    return os.path.join(STORAGE, digest, "key.pem")


def ensure_private_key(email):
    """One RSA key per Apple ID, kept forever. Apple ties the certificate to
    this key, so losing it means burning one of the account's two cert slots."""
    path = private_key_path(email)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    step("Generating a 2048-bit RSA key on this device (one time only)")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(path, "wb") as handle:
        handle.write(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    os.chmod(path, 0o600)
    return path


def key_modulus(path):
    return _hex(_load_key(path).private_numbers().public_numbers.n)


def cert_modulus(der_path):
    return _hex(_load_cert(der_path).public_key().public_numbers().n)


def list_ios_certs(session, team):
    certs = session.call(
        "listAllDevelopmentCerts", {"teamId": team["teamId"]}, key="certificates"
    )
    result = []
    for cert in certs:
        platform = (cert.get("certificatePlatform")
                    or (cert.get("certificateType") or {}).get("platform") or "ios")
        if str(platform).lower() == "ios":
            result.append(cert)
    return result


def obtain_certificate(session, team, key_path, workdir):
    """Return (cert_pem_path, was_reused).

    Mirrors isideload's rule: a certificate is reusable only when its
    machineName matches ours AND it was issued to this exact private key.
    """
    cert_der = os.path.join(workdir, "cert.der")
    cert_pem = os.path.join(workdir, "cert.pem")
    mine = key_modulus(key_path)

    def match(certs):
        for cert in certs:
            if not cert.get("certContent") or not cert.get("machineId"):
                continue
            if cert.get("machineName") != MACHINE_NAME:
                continue
            with open(cert_der, "wb") as handle:
                handle.write(bytes(cert["certContent"]))
            try:
                if cert_modulus(cert_der) == mine:
                    return cert
            except Fail:
                continue
        return None

    existing = list_ios_certs(session, team)
    found = match(existing)
    if found:
        _der_to_pem(cert_der, cert_pem)
        return cert_pem, True

    csr = _make_csr(_load_key(key_path)).decode()

    for attempt in range(4):
        try:
            result = session.call("submitDevelopmentCSR", {
                "teamId": team["teamId"],
                "csrContent": csr,
                "machineName": MACHINE_NAME,
                "machineId": str(uuid.uuid4()).upper(),
            }, key="certRequest")
        except PortalError as exc:
            # 7460: the account is already at its certificate limit. A free
            # account gets two, and Xcode/AltStore/SideStore each eat one.
            if exc.code != 7460 or attempt == 3:
                raise
            if not revoke_to_make_room(session, team):
                raise Fail(
                    "Apple says this account already has the maximum number of development\n"
                    "    certificates, and nothing was revoked. Free accounts get two — revoke one at\n"
                    "    https://developer.apple.com/account/resources/certificates and try again."
                )
            continue

        request_id = result["certRequestId"]
        for cert in list_ios_certs(session, team):
            if cert.get("certificateId") == request_id and cert.get("certContent"):
                with open(cert_der, "wb") as handle:
                    handle.write(bytes(cert["certContent"]))
                _der_to_pem(cert_der, cert_pem)
                return cert_pem, False
        raise Fail("Apple issued a certificate but would not hand it back.")

    raise Fail("Could not obtain a signing certificate.")


def revoke_to_make_room(session, team):
    """Ask before revoking: it breaks every app already signed with that
    certificate, including ones this script did not create."""
    certs = [c for c in list_ios_certs(session, team) if c.get("serialNumber")]
    if not certs:
        return False

    warn("Apple will not issue another certificate until an old one is revoked.")
    warn("Revoking one stops every app already signed with it from launching.")
    for index, cert in enumerate(certs, 1):
        info("  %d) %s — %s (expires %s)" % (
            index,
            cert.get("machineName") or "unnamed",
            cert.get("name") or cert.get("serialNumber"),
            cert.get("expirationDate", "?"),
        ))
    info("  0) cancel")

    choice = ask("Revoke which certificate?", "0")
    if not choice.isdigit() or not 1 <= int(choice) <= len(certs):
        return False
    victim = certs[int(choice) - 1]
    if not confirm("Really revoke '%s'?" % (victim.get("machineName") or victim["serialNumber"])):
        return False

    session.call("revokeDevelopmentCert", {
        "teamId": team["teamId"], "serialNumber": victim["serialNumber"],
    })
    ok("Revoked.")
    return True


# -- App ID and profile -------------------------------------------------------

def ensure_app_id(session, team, identifier, name):
    listing = session.call("listAppIds", {"teamId": team["teamId"]})
    for app_id in listing.get("appIds", []):
        if app_id.get("identifier") == identifier:
            return app_id

    available = listing.get("availableQuantity")
    if available is not None and available <= 0:
        raise Fail(
            "This account has no free App ID slots left (they expire after 7 days on a\n"
            "    free account). Delete one at\n"
            "    https://developer.apple.com/account/resources/identifiers and try again."
        )
    return session.call("addAppId", {
        "teamId": team["teamId"], "identifier": identifier, "name": name,
    }, key="appId")


def download_profile(session, team, app_id):
    profile = session.call("downloadTeamProvisioningProfile", {
        "teamId": team["teamId"], "appIdId": app_id["appIdId"],
    }, key="provisioningProfile")
    return bytes(profile["encodedProfile"])


def profile_entitlements(profile):
    plist = profile_plist(profile)
    entitlements = plist.get("Entitlements")
    if not entitlements:
        raise Fail("Apple's provisioning profile carries no entitlements — the App ID may "
                   "not be fully registered yet. Try again in a minute.")
    return entitlements


def profile_plist(data):
    """Pull the plist out of the CMS envelope a .mobileprovision is wrapped in.
    Same trick isideload uses: the payload is plain XML in the middle."""
    start = data.find(b"<plist")
    end = data.rfind(b"</plist>")
    if start < 0 or end < 0:
        raise Fail("The provisioning profile Apple returned is not readable.")
    return plistlib.loads(data[start:end + 8])


# ----------------------------------------------------------------------------
# The .ipa
# ----------------------------------------------------------------------------

