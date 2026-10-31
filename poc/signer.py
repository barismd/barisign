#!/usr/bin/env python3
# =============================================================================
#  BariSign — the signer (MIT)
#
#  Takes a downloaded IPA, a key.pem, cert.pem and profile.mobileprovision from
#  the portal stage, and produces an installable signed IPA plus a .zip that
#  carries it. Every step runs on the device, in a-Shell Python: stdlib plus the
#  `cryptography` that ships with a-Shell (3.4.8). No openssl, no native tools.
#
#  WHAT IT WRITES, per Mach-O binary
#  --------------------------------
#    * LC_CODE_SIGNATURE (added when the binary has none, reusing load-command
#      padding), and the blob it points at: an embedded-signature SuperBlob with
#      CodeDirectory (v0x20400, SHA-256, 4 KB pages), a RequirementSet, the XML
#      and DER entitlements, and the CMS SignedData.
#    * The CMS carries Apple's 1.2.840.113635.100.9.1 (plist of cdhashes) and
#      1.2.840.113635.100.9.2 (digest sequence) signed attributes, exactly as
#      Apple's codesign does.
#
#  WHAT IT WRITES, per bundle
#  -------------------------
#    * embedded.mobileprovision (the profile we were given), for the app and for
#      every app extension.
#    * _CodeSignature/CodeResources, with the same two sections Apple writes:
#      `files` (SHA-1, v1) and `files2` (SHA-256, v2), plus the stock rules.
#    * Nested bundles (Frameworks/*.framework, PlugIns/*.appex, *.dylib) are
#      signed first, then sealed by hashing their files in the parent.
#
#  WHAT IT NEVER DOES
#  ------------------
#    * No network. No Apple ID. No secret leaves the device.
#    * No third-party code: the encoding is written from the published formats
#      and checked against real Apple-signed IPAs (see docs/RESULTS.md).
#
#  STATUS: VERIFIED ON DEVICE: the signed IPA installed on a device, Trust
#          Developer was offered, and the app launched after the app-group fix.
#          The signer -> target Wi-Fi install (`sh bin/barisign pair`) is the
#          remaining untested path.
#
#  RUN IT
#  ------
#      sh bin/barisign sign                  guided (finds the IPA in the folder)
#      python3 -u poc/signer.py --ipa X.ipa [--profile P --key K --cert C]
#
#  The self-check runs at the end of every sign: the finished IPA is reopened and
#  every hash, CodeDirectory slot and CMS signature is recomputed from disk. If
#  that fails, the output is not offered.
# =============================================================================

import base64
import binascii
import datetime
import hashlib
import os
import plistlib
import shutil
import struct
import sys
import tempfile
import time
import traceback
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import NameOID

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(os.path.dirname(_HERE), "state")

VERSION = "0.1.0"

# Page size used for the code hashes. Apple's codesign emits 16 KB pages for
# these arm64/iOS binaries (its own AltStore and framework signatures do), so we
# match it rather than the 4 KB default some re-signers use. The device accepts
# both, but the system installer's validation is the one judge we cannot run.
PAGE_SIZE = 16384
PAGE_SHIFT = 14

# Mach-O
MH_MAGIC = 0xFEEDFACE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM = 0xCEFAEDFE
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_SEGMENT = 0x1
LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_UUID = 0x1B
MH_EXECUTE = 0x2

# Code signing magics (all big-endian on disk)
CSMAGIC_REQUIREMENTS = 0xFADE0C01
CSMAGIC_REQUIREMENT = 0xFADE0C00
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_EMBEDDED_ENTITLEMENTS = 0xFADE7171
CSMAGIC_EMBEDDED_DER_ENTITLEMENTS = 0xFADE7172
CSMAGIC_BLOBWRAPPER = 0xFADE0B01

CSSLOT_CODEDIRECTORY = 0
CSSLOT_INFOSLOT = 1
CSSLOT_REQUIREMENTS = 2
CSSLOT_RESOURCEDIR = 3
CSSLOT_ENTITLEMENTS = 5
CSSLOT_DER_ENTITLEMENTS = 7
CSSLOT_SIGNATURESLOT = 0x10000

CS_ADHOC = 0x2
CS_EXECSEG_MAIN_BINARY = 0x1
CS_EXECSEG_ALLOW_UNSIGNED = 0x10
CS_EXECSEG_CAN_LOAD_CDHASH = 0x100
CS_EXECSEG_CAN_EXEC_CDHASH = 0x200

# OIDs (DER encodings are built from these dotted strings)
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_DATA = "1.2.840.113549.1.7.1"
OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_SIGNING_TIME = "1.2.840.113549.1.9.5"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_RSA = "1.2.840.113549.1.1.1"
OID_CDHASHES_PLIST = "1.2.840.113635.100.9.1"
OID_CDHASHES = "1.2.840.113635.100.9.2"

# The public Apple CA certificates a development certificate can chain to.
# Downloaded from https://www.apple.com/certificateauthority/ (public data; a
# certificate is not code and fixes no version of anything). The signer picks the
# one whose subject equals the certificate's issuer and whose key verifies it.
APPLE_CA_DER = [
    ("AppleWWDRCAG2",
     "MIIC9zCCAnygAwIBAgIIb+/Y9emjp+4wCgYIKoZIzj0EAwIwZzEbMBkGA1UEAwwSQXBwbGUg"
     "Um9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTET"
     "MBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcNMTQwNTA2MjM0MzI0WhcNMjkw"
     "NTA2MjM0MzI0WjCBgDE0MDIGA1UEAwwrQXBwbGUgV29ybGR3aWRlIERldmVsb3BlciBSZWxh"
     "dGlvbnMgQ0EgLSBHMjEmMCQGA1UECwwdQXBwbGUgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkx"
     "EzARBgNVBAoMCkFwcGxlIEluYy4xCzAJBgNVBAYTAlVTMFkwEwYHKoZIzj0CAQYIKoZIzj0D"
     "AQcDQgAE3fC3BkvP3XMEE8RDiQOTgPte9nStQmFSWAImUxnIYyIHCVJhysTZV+9tJmiLdJGM"
     "xPmAaCj8CWjwENrp0C7JGqOB9zCB9DBGBggrBgEFBQcBAQQ6MDgwNgYIKwYBBQUHMAGGKmh0"
     "dHA6Ly9vY3NwLmFwcGxlLmNvbS9vY3NwMDQtYXBwbGVyb290Y2FnMzAdBgNVHQ4EFgQUhLaE"
     "zDqGYnIWWZToGqO9SN863wswDwYDVR0TAQH/BAUwAwEB/zAfBgNVHSMEGDAWgBS7sN6hWDOI"
     "mqSKmd6+veuv2sskqzA3BgNVHR8EMDAuMCygKqAohiZodHRwOi8vY3JsLmFwcGxlLmNvbS9h"
     "cHBsZXJvb3RjYWczLmNybDAOBgNVHQ8BAf8EBAMCAQYwEAYKKoZIhvdjZAYCDwQCBQAwCgYI"
     "KoZIzj0EAwIDaQAwZgIxANmxxzHGI/ZPTdDZR8V9GGkRh3En02it4Jtlmr5s3z9GppAJvm6h"
     "OyywUYlBPIfSvwIxAPxkUolLPF2/axzCiZgvcq61m6oaCyNUd1ToFUOixRLal1BzfF7QbrJc"
     "YlDXUfE6Wg=="),
    ("AppleWWDRCAG3",
     "MIIEUTCCAzmgAwIBAgIQfK9pCiW3Of57m0R6wXjF7jANBgkqhkiG9w0BAQsFADBiMQswCQYD"
     "VQQGEwJVUzETMBEGA1UEChMKQXBwbGUgSW5jLjEmMCQGA1UECxMdQXBwbGUgQ2VydGlmaWNh"
     "dGlvbiBBdXRob3JpdHkxFjAUBgNVBAMTDUFwcGxlIFJvb3QgQ0EwHhcNMjAwMjE5MTgxMzQ3"
     "WhcNMzAwMjIwMDAwMDAwWjB1MUQwQgYDVQQDDDtBcHBsZSBXb3JsZHdpZGUgRGV2ZWxvcGVy"
     "IFJlbGF0aW9ucyBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTELMAkGA1UECwwCRzMxEzARBgNV"
     "BAoMCkFwcGxlIEluYy4xCzAJBgNVBAYTAlVTMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIB"
     "CgKCAQEA2PWJ/KhZC4fHTJEuLVaQ03gdpDDppUjvC0O/LYT7JF1FG+XrWTYSXFRknmxiLbTG"
     "l8rMPPbWBpH85QKmHGq0edVny6zpPwcR4YS8Rx1mjjmi6LRJ7TrS4RBgeo6TjMrA2gzAg9Dj"
     "+ZHWp4zIwXPirkbRYp2SqJBgN31ols2N4Pyb+ni743uvLRfdW/6AWSN1F7gSwe0b5TTO/iK1"
     "nkmw5VW/j4SiPKi6xYaVFuQAyZ8D0MyzOhZ71gVcnetHrg21LYwOaU1A0EtMOwSejSGxrC5D"
     "VDDOwYqGlJhL32oNP/77HK6XF8J4CjDgXx9UO0m3JQAaN4LSVpelUkl8YDib7wIDAQABo4Hv"
     "MIHsMBIGA1UdEwEB/wQIMAYBAf8CAQAwHwYDVR0jBBgwFoAUK9BpR5R2Cf70a40uQKb3R01/"
     "CF4wRAYIKwYBBQUHAQEEODA2MDQGCCsGAQUFBzABhihodHRwOi8vb2NzcC5hcHBsZS5jb20v"
     "b2NzcDAzLWFwcGxlcm9vdGNhMC4GA1UdHwQnMCUwI6AhoB+GHWh0dHA6Ly9jcmwuYXBwbGUu"
     "Y29tL3Jvb3QuY3JsMB0GA1UdDgQWBBQJ/sAVkPmvZAqSErkmKGMMl+ynsjAOBgNVHQ8BAf8E"
     "BAMCAQYwEAYKKoZIhvdjZAYCAQQCBQAwDQYJKoZIhvcNAQELBQADggEBAK1lE+j24IF3RAJH"
     "Qr5fpTkg6mKp/cWQyXMT1Z6b0KoPjY3L7QHPbChAW8dVJEH4/M/BtSPp3Ozxb8qAHXfCxGFJ"
     "JWevD8o5Ja3T43rMMygNDi6hV0Bz+uZcrgZRKe3jhQxPYdwyFot30ETKXXIDMUacrptAGvr0"
     "4NM++i+MZp+XxFRZ79JI9AeZSWBZGcfdlNHAwWx/eCHvDOs7bJmCS1JgOLU5gm3sUjFTvg+R"
     "TElJdI+mUcuER04ddSduvfnSXPN/wmwLCTbiZOTCNwMUGdXqapSqqdv+9poIZ4vvK7iqF0mD"
     "r8/LvOnP6pVxsLRFoszlh6oKw0E6eVzaUDSdlTs="),
    ("AppleWWDRCAG4",
     "MIIEVTCCAz2gAwIBAgIUE9x3lVJx5T3GMujM/+Uh88zFztIwDQYJKoZIhvcNAQELBQAwYjEL"
     "MAkGA1UEBhMCVVMxEzARBgNVBAoTCkFwcGxlIEluYy4xJjAkBgNVBAsTHUFwcGxlIENlcnRp"
     "ZmljYXRpb24gQXV0aG9yaXR5MRYwFAYDVQQDEw1BcHBsZSBSb290IENBMB4XDTIwMTIxNjE5"
     "MzYwNFoXDTMwMTIxMDAwMDAwMFowdTFEMEIGA1UEAww7QXBwbGUgV29ybGR3aWRlIERldmVs"
     "b3BlciBSZWxhdGlvbnMgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxCzAJBgNVBAsMAkc0MRMw"
     "EQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzCCASIwDQYJKoZIhvcNAQEBBQADggEP"
     "ADCCAQoCggEBANAfeKp6JzKwRl/nF3bYoJ0OKY6tPTKlxGs3yeRBkWq3eXFdDDQEYHX3rkOP"
     "R8SGHgjov9Y5Ui8eZ/xx8YJtPH4GUnadLLzVQ+mxtLxAOnhRXVGhJeG+bJGdayFZGEHVD41t"
     "QSo5SiHgkJ9OE0/QjJoyuNdqkh4laqQyziIZhQVg3AJK8lrrd3kCfcCXVGySjnYB5kaP5eYq"
     "+6KwrRitbTOFOCOL6oqW7Z+uZk+jDEAnbZXQYojZQykn/e2kv1MukBVlPNkuYmQzHWxq3Y4h"
     "qqRfFcYw7V/mjDaSlLfcOQIA+2SM1AyB8j/VNJeHdSbCb64DYyEMe9QbsWLFApy9/a8CAwEA"
     "AaOB7zCB7DASBgNVHRMBAf8ECDAGAQH/AgEAMB8GA1UdIwQYMBaAFCvQaUeUdgn+9GuNLkCm"
     "90dNfwheMEQGCCsGAQUFBwEBBDgwNjA0BggrBgEFBQcwAYYoaHR0cDovL29jc3AuYXBwbGUu"
     "Y29tL29jc3AwMy1hcHBsZXJvb3RjYTAuBgNVHR8EJzAlMCOgIaAfhh1odHRwOi8vY3JsLmFw"
     "cGxlLmNvbS9yb290LmNybDAdBgNVHQ4EFgQUW9n6HeeaGgujmXYiUIY+kchbd6gwDgYDVR0P"
     "AQH/BAQDAgEGMBAGCiqGSIb3Y2QGAgEEAgUAMA0GCSqGSIb3DQEBCwUAA4IBAQA/Vj2e5bbD"
     "eeZFIGi9v3OLLBKeAuOugCKMBB7DUshwgKj7zqew1UJEggOCTwb8O0kU+9h0UoWvp50h5wES"
     "A5/NQFjQAde/MoMrU1goPO6cn1R2PWQnxn6NHThNLa6B5rmluJyJlPefx4elUWY0GzlxOSTj"
     "h2fvpbFoe4zuPfeutnvi0v/fYcZqdUmVIkSoBPyUuAsuORFJEtHlgepZAE9bPFo22noicwkJ"
     "ac3AfOriJP6YRLj477JxPxpd1F1+M02cHSS+APCQA1iZQT0xWmJArzmoUUOSqwSonMJNsUvS"
     "q3xKX+udO7xPiEAGE/+QF4oIRynoYpgppU8RBWk6z/Kf"),
    ("AppleWWDRCAG5",
     "MIIEVTCCAz2gAwIBAgIUO36ACu7TAqHm7NuX2cqsKJzxaZQwDQYJKoZIhvcNAQELBQAwYjEL"
     "MAkGA1UEBhMCVVMxEzARBgNVBAoTCkFwcGxlIEluYy4xJjAkBgNVBAsTHUFwcGxlIENlcnRp"
     "ZmljYXRpb24gQXV0aG9yaXR5MRYwFAYDVQQDEw1BcHBsZSBSb290IENBMB4XDTIwMTIxNjE5"
     "Mzg1NloXDTMwMTIxMDAwMDAwMFowdTFEMEIGA1UEAww7QXBwbGUgV29ybGR3aWRlIERldmVs"
     "b3BlciBSZWxhdGlvbnMgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkxCzAJBgNVBAsMAkc1MRMw"
     "EQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzCCASIwDQYJKoZIhvcNAQEBBQADggEP"
     "ADCCAQoCggEBAJ9d2h/7+rzQSyI8x9Ym+hf39J8ePmQRZprvXr6rNL2qLCFu1h6UIYUsdMEO"
     "EGGqPGNKfkrjyHXWz8KcCEh7arkpsclm/ciKFtGyBDyCuoBs4v8Kcuus/jtvSL6eixFNlX2y"
     "e5AvAhxO/Em+12+1T754xtress3J2WYRO1rpCUVziVDUTuJoBX7adZxLAa7a489tdE3eU9DV"
     "GjiCOtCd410pe7GB6iknC/tgfIYS+/BiTwbnTNEf2W2e7XPaeCENnXDZRleQX2eEwXN3Cqhi"
     "YraucIa7dSOJrXn25qTU/YMmMgo7JJJbIKGc0S+AGJvdPAvntf3sgFcPF54/K4cnu/cCAwEA"
     "AaOB7zCB7DASBgNVHRMBAf8ECDAGAQH/AgEAMB8GA1UdIwQYMBaAFCvQaUeUdgn+9GuNLkCm"
     "90dNfwheMEQGCCsGAQUFBwEBBDgwNjA0BggrBgEFBQcwAYYoaHR0cDovL29jc3AuYXBwbGUu"
     "Y29tL29jc3AwMy1hcHBsZXJvb3RjYTAuBgNVHR8EJzAlMCOgIaAfhh1odHRwOi8vY3JsLmFw"
     "cGxlLmNvbS9yb290LmNybDAdBgNVHQ4EFgQUGYuXjUpbYXhX9KVcNRKKOQjjsHUwDgYDVR0P"
     "AQH/BAQDAgEGMBAGCiqGSIb3Y2QGAgEEAgUAMA0GCSqGSIb3DQEBCwUAA4IBAQBaxDWi2eYK"
     "nlKiAIIid81yL5D5Iq8UJcyqCkJgksK9dR3rTMoV5X5rQBBe+1tFdA3wen2Ikc7eY4tCidIY"
     "30GzWJ4GCIdI3UCvI9Xt6yxg5eukfxzpnIPWlF9MYjmKTq4TjX1DuNxerL4YQPLmDyxdE5Px"
     "e2WowmhI3v+0lpsM+zI2np4NlV84CouW0hJst4sLjtc+7G8Bqs5NRWDbhHFmYuUZZTDNiv9F"
     "U/tu+4h3Q8NIY/n3UbNyXnniVs+8u4S5OFp4rhFIUrsNNYuU3sx0mmj1SWCUrPKosxWGkNDM"
     "MEOG0+VwAlG0gcCol9Tq6rCMCUDvOJOyzSID62dDZchF"),
    ("AppleWWDRCAG6",
     "MIIDFjCCApygAwIBAgIUIsGhRwp0c2nvU4YSycafPTjzbNcwCgYIKoZIzj0EAwMwZzEbMBkG"
     "A1UEAwwSQXBwbGUgUm9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9u"
     "IEF1dGhvcml0eTETMBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcNMjEwMzE3"
     "MjAzNzEwWhcNMzYwMzE5MDAwMDAwWjB1MUQwQgYDVQQDDDtBcHBsZSBXb3JsZHdpZGUgRGV2"
     "ZWxvcGVyIFJlbGF0aW9ucyBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTELMAkGA1UECwwCRzYx"
     "EzARBgNVBAoMCkFwcGxlIEluYy4xCzAJBgNVBAYTAlVTMHYwEAYHKoZIzj0CAQYFK4EEACID"
     "YgAEbsQKC94PrlWmZXnXgtxzdVJL8T0SGYngDRGpngn3N6PT8JMEb7FDi4bBmPhCnZ3/sq6P"
     "F/cGcKXWsL5vOteRhyJ45x3ASP7cOB+aao90fcpxSv/EZFbniAbNgZGhIhpIo4H6MIH3MBIG"
     "A1UdEwEB/wQIMAYBAf8CAQAwHwYDVR0jBBgwFoAUu7DeoVgziJqkipnevr3rr9rLJKswRgYI"
     "KwYBBQUHAQEEOjA4MDYGCCsGAQUFBzABhipodHRwOi8vb2NzcC5hcHBsZS5jb20vb2NzcDAz"
     "LWFwcGxlcm9vdGNhZzMwNwYDVR0fBDAwLjAsoCqgKIYmaHR0cDovL2NybC5hcHBsZS5jb20v"
     "YXBwbGVyb290Y2FnMy5jcmwwHQYDVR0OBBYEFD8vlCNR01DJmig97bB85c+lkGKZMA4GA1Ud"
     "DwEB/wQEAwIBBjAQBgoqhkiG92NkBgIBBAIFADAKBggqhkjOPQQDAwNoADBlAjBAXhSq5IyK"
     "ogMCPtw490BaB677CaEGJXufQB/EqZGd6CSjiCtOnuMTbXVXmxxcxfkCMQDTSPxarZXvNrkx"
     "U3TkUMI33yzvFVVRT4wxWJC994OsdcZ4+RGNsYDyR5gmdr0nDGg="),
    ("AppleRootCA",
     "MIIEuzCCA6OgAwIBAgIBAjANBgkqhkiG9w0BAQUFADBiMQswCQYDVQQGEwJVUzETMBEGA1UE"
     "ChMKQXBwbGUgSW5jLjEmMCQGA1UECxMdQXBwbGUgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkx"
     "FjAUBgNVBAMTDUFwcGxlIFJvb3QgQ0EwHhcNMDYwNDI1MjE0MDM2WhcNMzUwMjA5MjE0MDM2"
     "WjBiMQswCQYDVQQGEwJVUzETMBEGA1UEChMKQXBwbGUgSW5jLjEmMCQGA1UECxMdQXBwbGUg"
     "Q2VydGlmaWNhdGlvbiBBdXRob3JpdHkxFjAUBgNVBAMTDUFwcGxlIFJvb3QgQ0EwggEiMA0G"
     "CSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDkkakJH5HbHkdQ6wXtXnmELes2oldMVeyLGYne"
     "+Uts9QerIjAC6Bg++FAJ039BqJj50cpmnCRrEdCju+QbKsMflZ56DKRHi1vUFjczy8QPTc4U"
     "adHJGXL1XQ7Vf1+b8iUDulWPTV0N8WQ1IxVLFVkds5T39pyez1C6wVhQZ48ItCD3y6wsIG9w"
     "tj8BMIy3Q88PnT3zK0koGsj+zrW5DtleHNbLPbU6rfQPDgCSC7EhFi501TwN22IWq6NxkkdT"
     "VcGvL0Gz+PvjcM3mo0xFfh9Ma1CWQYnEdGILEINBhzOKgbEwWOxaBDKMaLOPHd5lc/9nXmW8"
     "Sdh2nzMUZaF3lMktAgMBAAGjggF6MIIBdjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUw"
     "AwEB/zAdBgNVHQ4EFgQUK9BpR5R2Cf70a40uQKb3R01/CF4wHwYDVR0jBBgwFoAUK9BpR5R2"
     "Cf70a40uQKb3R01/CF4wggERBgNVHSAEggEIMIIBBDCCAQAGCSqGSIb3Y2QFATCB8jAqBggr"
     "BgEFBQcCARYeaHR0cHM6Ly93d3cuYXBwbGUuY29tL2FwcGxlY2EvMIHDBggrBgEFBQcCAjCB"
     "thqBs1JlbGlhbmNlIG9uIHRoaXMgY2VydGlmaWNhdGUgYnkgYW55IHBhcnR5IGFzc3VtZXMg"
     "YWNjZXB0YW5jZSBvZiB0aGUgdGhlbiBhcHBsaWNhYmxlIHN0YW5kYXJkIHRlcm1zIGFuZCBj"
     "b25kaXRpb25zIG9mIHVzZSwgY2VydGlmaWNhdGUgcG9saWN5IGFuZCBjZXJ0aWZpY2F0aW9u"
     "IHByYWN0aWNlIHN0YXRlbWVudHMuMA0GCSqGSIb3DQEBBQUAA4IBAQBcNplMLXi37Yyb3PN3"
     "m/J20ncwT8EfhYOFG5k9RzfyqZtAjizUsZAS2L70c5vu0mQPy3lPNNiiPvl4/2vIB+x9OYOL"
     "UyDTOMSxv5pPCmv/K/xZpwUJfBdAVhEedNO3iyM7R6PVbyTi69G3cN8PReEnyvFteO3ntRcX"
     "qNx+IjXKJdXZD9Zr1KIkIxH3oayPc4FgxhtbCS+SsvhESPBgOJ4V9T0mZyCKM2r3DYLP3uuj"
     "L/lTaltkwGMzd/c6ByxW69oPIQ7aunMZT7XZNn/Bh1XZp5m5MkL72NVxnn6hUrcbvZNCJBIq"
     "xw8dtk2cXmPIS4AXUKqK1drk/NAJBzewdXUh"),
    ("AppleRootCAG3",
     "MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwSQXBwbGUg"
     "Um9vdCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTET"
     "MBEGA1UECgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcNMTQwNDMwMTgxOTA2WhcNMzkw"
     "NDMwMTgxOTA2WjBnMRswGQYDVQQDDBJBcHBsZSBSb290IENBIC0gRzMxJjAkBgNVBAsMHUFw"
     "cGxlIENlcnRpZmljYXRpb24gQXV0aG9yaXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYD"
     "VQQGEwJVUzB2MBAGByqGSM49AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHc"
     "FBbZDuWmBSp3ZHtfTjjTuxxEtX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dv"
     "MVztK517IDvYuVTZXpmkOlEKMaNCMEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySr"
     "MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMDA2gAMGUCMQCD"
     "6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3meoyhpmvOwgPUnPWTxnS4at+qIxUCMG1mihDK"
     "1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkLF1vLUagM6BgD56KyKA=="),
]

_CA_CACHE = None

# Apple's stock resource rules, as they appear in CodeResources written by
# codesign on iOS. Kept literal so the output is comparable, not "close enough".
RULES_V1 = {
    "^.*": True,
    "^.*\\.lproj/": {"optional": True, "weight": 1000.0},
    "^.*\\.lproj/locversion.plist$": {"omit": True, "weight": 1100.0},
    "^Base\\.lproj/": {"weight": 1010.0},
    "^version.plist$": True,
}

RULES_V2 = {
    ".*\\.dSYM($|/)": {"weight": 11.0},
    "^(.*/)?\\.DS_Store$": {"omit": True, "weight": 2000.0},
    "^.*": True,
    "^.*\\.lproj/": {"optional": True, "weight": 1000.0},
    "^.*\\.lproj/locversion.plist$": {"omit": True, "weight": 1100.0},
    "^Base\\.lproj/": {"weight": 1010.0},
    "^Info\\.plist$": {"omit": True, "weight": 20.0},
    "^PkgInfo$": {"omit": True, "weight": 20.0},
    "^embedded\\.provisionprofile$": {"weight": 20.0},
    "^version\\.plist$": {"weight": 20.0},
}


class SigningError(Exception):
    """Anything the user can act on: a missing file, a bad profile, a bug."""


def _say(message):
    """Default progress sink; barisign.py replaces it with its moving line."""
    return None


# ---------------------------------------------------------------------------
# DER (just enough of it: definite lengths, what CMS and entitlements need)
# ---------------------------------------------------------------------------

def der_length(size):
    if size < 0x80:
        return bytes([size])
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def der(tag, content):
    return bytes([tag]) + der_length(len(content)) + content


def der_integer(value, minimal=True):
    if value < 0:
        raise SigningError("negative integers are not used here")
    if value == 0:
        raw = b"\x00"
    else:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if minimal and raw[0] & 0x80:
            raw = b"\x00" + raw
    return der(0x02, raw)


def der_oid(dotted):
    parts = [int(part) for part in dotted.split(".")]
    if len(parts) < 2:
        raise SigningError("bad OID %r" % dotted)
    body = bytearray([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        chunks = []
        while True:
            chunks.insert(0, part & 0x7F)
            part >>= 7
            if not part:
                break
        for index in range(len(chunks) - 1):
            chunks[index] |= 0x80
        body.extend(chunks)
    return der(0x06, bytes(body))


def der_read(data, offset=0, depth=0):
    """Return (tag, content, next_offset) for one element.

    Apple's codesign emits BER indefinite lengths (0x80) with an end-of-contents
    marker in the CMS it writes, so both definite and indefinite lengths are
    accepted here. The signer itself always writes definite lengths.
    """
    if depth > 16:
        raise SigningError("DER nested too deeply")
    if offset >= len(data):
        raise SigningError("truncated DER")
    tag = data[offset]
    offset += 1
    if offset >= len(data):
        raise SigningError("truncated DER length")
    first = data[offset]
    offset += 1
    if first == 0x80:
        start = offset
        position = offset
        while True:
            if position + 2 > len(data):
                raise SigningError("unterminated indefinite-length value")
            if data[position] == 0x00 and data[position + 1] == 0x00:
                return tag, data[start:position], position + 2
            _tag, _value, position = der_read(data, position, depth + 1)
    if first & 0x80:
        count = first & 0x7F
        if count > 4:
            raise SigningError("unsupported DER length")
        if offset + count > len(data):
            raise SigningError("truncated DER length")
        size = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    else:
        size = first
    end = offset + size
    if end > len(data):
        raise SigningError("truncated DER value")
    return tag, data[offset:end], end


def der_children(content):
    items = []
    offset = 0
    while offset < len(content):
        tag, value, offset = der_read(content, offset)
        items.append((tag, value))
    return items


def der_encode(tag, content):
    return der(tag, content)


# ---------------------------------------------------------------------------
# plists, profiles, certificates
# ---------------------------------------------------------------------------

def read_plist(path):
    """Return (dictionary, raw bytes). Raises SigningError with the file name."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SigningError("cannot read %s: %s" % (path, exc))
    try:
        return plistlib.loads(raw), raw
    except Exception as exc:                                    # noqa: BLE001
        raise SigningError("not a property list: %s (%s)" % (path, str(exc)[:80]))


def write_plist(path, value, prefer_binary):
    fmt = plistlib.FMT_BINARY if prefer_binary else plistlib.FMT_XML
    data = plistlib.dumps(value, fmt=fmt, sort_keys=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return data


def read_mobileprovision(path):
    """The profile is a CMS SignedData whose content is an XML plist.

    Apple has shipped both XML and binary payloads; the plist is located inside
    the container instead of being assumed to start at byte zero.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError as exc:
        raise SigningError("cannot read the profile %s: %s" % (path, exc))

    for marker in (b"<?xml", b"<!DOCTYPE plist", b"<plist"):
        text = blob.find(marker)
        if text >= 0:
            end = blob.find(b"</plist>", text)
            if end >= 0:
                try:
                    return plistlib.loads(blob[text:end + len(b"</plist>")])
                except Exception as exc:                        # noqa: BLE001
                    raise SigningError("the profile's plist is unreadable: %s" % (exc,))
            break
    binary = blob.find(b"bplist00")
    if binary >= 0:
        try:
            return plistlib.loads(blob[binary:])
        except Exception as exc:                                # noqa: BLE001
            raise SigningError("the profile's plist is unreadable: %s" % (exc,))
    # A bare plist (handy for tests) is accepted too.
    try:
        return plistlib.loads(blob)
    except Exception:                                           # noqa: BLE001
        raise SigningError("this is not a provisioning profile: %s" % path)


def load_certificate(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SigningError("cannot read the certificate %s: %s" % (path, exc))
    try:
        if raw.lstrip().startswith(b"-----BEGIN"):
            return x509.load_pem_x509_certificate(raw)
        return x509.load_der_x509_certificate(raw)
    except Exception as exc:                                    # noqa: BLE001
        raise SigningError("not an X.509 certificate: %s (%s)" % (path, str(exc)[:80]))


def load_private_key(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise SigningError("cannot read the key %s: %s" % (path, exc))
    try:
        return serialization.load_pem_private_key(raw, password=None)
    except Exception as exc:                                    # noqa: BLE001
        raise SigningError("not a usable private key: %s (%s)" % (path, str(exc)[:80]))


def apple_cas():
    global _CA_CACHE
    if _CA_CACHE is None:
        result = []
        for name, encoded in APPLE_CA_DER:
            try:
                der_bytes = base64.b64decode(encoded)
            except (binascii.Error, ValueError) as exc:
                raise SigningError("embedded Apple CA %s is corrupt: %s" % (name, exc))
            try:
                result.append((name, x509.load_der_x509_certificate(der_bytes)))
            except Exception as exc:                            # noqa: BLE001
                raise SigningError("embedded Apple CA %s does not parse: %s" % (name, exc))
        _CA_CACHE = result
    return _CA_CACHE


def _verify_with(key, signature, data, digest):
    if isinstance(key, rsa.RSAPublicKey):
        key.verify(signature, data, padding.PKCS1v15(), digest)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key.verify(signature, data, ec.ECDSA(digest))
    else:
        raise SigningError("unsupported certificate key type: %r" % (key,))


def certificate_chain(leaf):
    """Leaf, its issuer, and (when known) that issuer's issuer.

    The whole chain is shipped in the CMS, exactly as Apple ships it, so the
    device never has to fetch anything.
    """
    chain = [leaf]
    current = leaf
    for _ in range(2):
        issuer_name = current.issuer
        found = None
        for _name, candidate in apple_cas():
            if candidate.subject != issuer_name:
                continue
            try:
                _verify_with(candidate.public_key(), current.signature,
                             current.tbs_certificate_bytes,
                             current.signature_hash_algorithm)
            except Exception:                                   # noqa: BLE001
                continue
            found = candidate
            break
        if found is None:
            if current.subject == current.issuer:
                break                       # self-signed leaf (tests only)
            names = issuer_name.rfc4514_string()
            raise SigningError(
                "this certificate was issued by \"%s\", which is not one of the "
                "Apple CAs built into BariSign. Update the app, or use the "
                "certificate the portal issued." % names[:90])
        chain.append(found)
        if found.subject == found.issuer:
            break
        current = found
    return chain


# ---------------------------------------------------------------------------
# CMS SignedData with Apple's two code-signing attributes
# ---------------------------------------------------------------------------

def _attribute(oid, values):
    return der(0x30, der_oid(oid) + der(0x31, b"".join(values)))


def _algorithm(oid):
    return der(0x30, der_oid(oid) + b"\x05\x00")


def _cdhash_plist(digest):
    value = {"cdhashes": [digest[:20]]}
    # plistlib already ends the document with a newline, like Apple's writer.
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def build_cms(code_directory, leaf, chain, key):
    """Detached CMS SignedData over the CodeDirectory blob.

    Signed attributes, in the order Apple writes them:
      contentType(id-data), signingTime, messageDigest,
      1.2.840.113635.100.9.2 (digest in ASN.1), 1.2.840.113635.100.9.1 (plist).
    """
    digest = hashlib.sha256(code_directory).digest()

    # IssuerAndSerialNumber, taken raw from the certificate so the encoding is
    # exactly the one Apple used when it issued it.
    _tag, cert_body, _end = der_read(leaf.public_bytes(serialization.Encoding.DER), 0)
    tbs_tag, tbs_body, _ = der_read(cert_body, 0)
    items = der_children(tbs_body)
    index = 0
    if items[0][0] == 0xA0:
        index = 1
    serial_tag, serial_value = items[index]
    issuer_tag, issuer_value = items[index + 2]
    issuer_and_serial = der(0x30, der_encode(issuer_tag, issuer_value) + der(serial_tag, serial_value))

    attributes = [
        _attribute(OID_CONTENT_TYPE, [der_oid(OID_DATA)]),
        _attribute(OID_SIGNING_TIME, [der(0x17, time.strftime("%y%m%d%H%M%SZ", time.gmtime()).encode())]),
        _attribute(OID_MESSAGE_DIGEST, [der(0x04, digest)]),
        _attribute(OID_CDHASHES, [der(0x30, der_oid(OID_SHA256) + der(0x04, digest))]),
        _attribute(OID_CDHASHES_PLIST, [der(0x04, _cdhash_plist(digest))]),
    ]
    # DER SET OF is sorted by encoding; the signature covers exactly these bytes.
    attributes.sort()
    signed_attrs = der(0x31, b"".join(attributes))

    signature = key.sign(signed_attrs, padding.PKCS1v15(), hashes.SHA256())

    signer_info = der(0x30,
                      der_integer(1) +
                      issuer_and_serial +
                      _algorithm(OID_SHA256) +
                      # [0] IMPLICIT, same content as the SET above
                      der(0xA0, b"".join(attributes)) +
                      _algorithm(OID_RSA) +
                      der(0x04, signature))

    certificates = sorted(cert.public_bytes(serialization.Encoding.DER) for cert in chain)

    signed_data = der(0x30,
                      der_integer(1) +
                      der(0x31, _algorithm(OID_SHA256)) +
                      der(0x30, der_oid(OID_DATA)) +
                      der(0xA0, b"".join(certificates)) +
                      der(0x31, signer_info))

    return der(0x30, der_oid(OID_SIGNED_DATA) + der(0xA0, signed_data))


# ---------------------------------------------------------------------------
# DER entitlements (Apple's plist-in-ASN.1, as codesign writes it)
# ---------------------------------------------------------------------------

def der_plist_value(value):
    if isinstance(value, bool):
        return der(0x01, b"\xff" if value else b"\x00")
    if isinstance(value, str):
        return der(0x0C, value.encode("utf-8"))
    if isinstance(value, int):
        return der_integer(value)
    if isinstance(value, (bytes, bytearray)):
        return der(0x04, bytes(value))
    if isinstance(value, list):
        return der(0x30, b"".join(der_plist_value(item) for item in value))
    if isinstance(value, dict):
        return der(0xB0, _der_plist_entries(value))
    raise SigningError("this entitlement value cannot be encoded: %r" % (value,))


def _der_plist_entries(mapping):
    entries = []
    for name in sorted(mapping):
        entries.append(der(0x30,
                           der(0x0C, name.encode("utf-8")) +
                           der_plist_value(mapping[name])))
    return b"".join(entries)


def der_entitlements(value):
    return der(0x70, der_integer(1) + der(0xB0, _der_plist_entries(value)))


def entitlements_xml(value):
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


# ---------------------------------------------------------------------------
# CodeDirectory, requirements and the embedded-signature superblob
# ---------------------------------------------------------------------------

def _blob(magic, payload):
    return struct.pack(">II", magic, len(payload) + 8) + payload


def _padded_bytes(raw):
    padding_bytes = (4 - len(raw) % 4) % 4
    return struct.pack(">I", len(raw)) + raw + b"\x00" * padding_bytes


def _padded_string(text):
    return _padded_bytes(text.encode("utf-8"))


def build_requirements(identifier, common_name):
    """The designated requirement Apple's codesign derives from the leaf cert.

    expression: identifier "X" and anchor apple generic
                and certificate leaf[subject.CN] = "CN"
                and certificate 1[field.1.2.840.113635.100.6.2.1] exists
    """
    wwdr = bytes([0x2A, 0x86, 0x48, 0x86, 0xF7, 0x63, 0x64, 0x06, 0x02, 0x01])
    expression = struct.pack(">I", 1)                       # exprForm
    expression += struct.pack(">I", 6) + struct.pack(">I", 2) + _padded_string(identifier)
    expression += struct.pack(">I", 6) + struct.pack(">I", 15)   # and anchor apple generic
    expression += struct.pack(">I", 6) + struct.pack(">I", 11) + struct.pack(">I", 0)
    expression += _padded_string("subject.CN") + struct.pack(">I", 1) + _padded_string(common_name)
    expression += struct.pack(">I", 14) + struct.pack(">I", 1) + _padded_bytes(wwdr)
    expression += struct.pack(">I", 0)                      # matchExists

    inner = _blob(CSMAGIC_REQUIREMENT, expression)
    body = struct.pack(">III", CSMAGIC_REQUIREMENTS, 20 + len(inner), 1)
    body += struct.pack(">II", 3, 20)                       # designated requirement
    return body + inner


def build_code_directory(identifier, team, code_limit, specials, page_hashes,
                         exec_base, exec_limit, exec_flags):
    """CodeDirectory v0x20400, SHA-256, 4 KB pages: the layout iOS 27 verifies."""
    ident = identifier.encode("utf-8") + b"\x00"
    team_bytes = team.encode("utf-8") + b"\x00" if team else b""
    highest = max(specials) if specials else 0

    header_size = 0x58
    ident_offset = header_size
    team_offset = header_size + len(ident) if team else 0
    digest_offset = header_size + len(ident) + len(team_bytes) + highest * 32
    total_size = digest_offset + len(page_hashes) * 32

    data = bytearray()
    data += struct.pack(">IIIIIIIII", CSMAGIC_CODEDIRECTORY, total_size, 0x20400, 0,
                        digest_offset, ident_offset, highest, len(page_hashes), code_limit)
    data += struct.pack(">BBBB", 32, 2, 0, PAGE_SHIFT)       # hashSize, SHA-256, platform, page shift
    data += struct.pack(">IIII", 0, 0, team_offset, 0)       # spare2, scatter, team, spare3
    data += struct.pack(">QQQ", 0, exec_base, exec_limit)    # codeLimit64, execSegBase, execSegLimit
    data += struct.pack(">Q", exec_flags)
    assert len(data) == header_size

    data += ident
    data += team_bytes
    for slot in range(highest, 0, -1):
        data += specials.get(slot, b"\x00" * 32)
    for digest in page_hashes:
        data += digest
    return bytes(data)


def build_superblob(blobs):
    blobs = sorted(blobs, key=lambda item: item[0])
    index_size = 12 + 8 * len(blobs)
    index = b""
    body = b""
    for slot, payload in blobs:
        index += struct.pack(">II", slot, index_size + len(body))
        body += payload
    total = index_size + len(body)
    return struct.pack(">III", CSMAGIC_EMBEDDED_SIGNATURE, total, len(blobs)) + index + body


# ---------------------------------------------------------------------------
# Mach-O reading and rewriting
# ---------------------------------------------------------------------------

def is_macho(data):
    if len(data) < 8:
        return False
    magic = data[:4]
    return magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                     b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                     b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                     b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")


def looks_like_macho_file(path):
    try:
        with open(path, "rb") as handle:
            return is_macho(handle.read(8))
    except OSError:
        return False


def _u32(data, offset, little):
    return struct.unpack_from("<I" if little else ">I", data, offset)[0]


def _u64(data, offset, little):
    return struct.unpack_from("<Q" if little else ">Q", data, offset)[0]


class ThinMachO:
    """The parts of one Mach-O slice this signer reads and patches."""

    def __init__(self, data):
        self.data = data
        if len(data) < 32:
            raise SigningError("a Mach-O file is truncated")
        raw = data[:4]
        if raw == b"\xcf\xfa\xed\xfe":
            self.little, self.bits = True, 64
        elif raw == b"\xfe\xed\xfa\xcf":
            self.little, self.bits = False, 64
        elif raw in (b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce"):
            raise SigningError("this IPA contains a 32-bit binary; iOS 27 cannot run it")
        else:
            raise SigningError("not a Mach-O file")

        self.filetype = _u32(data, 12, self.little)
        self.ncmds = _u32(data, 16, self.little)
        self.sizeofcmds = _u32(data, 20, self.little)
        self.header_size = 32
        self.commands = []
        self.segments = {}
        self.section_offsets = []
        self.code_signature = None
        self.uuid = None

        offset = self.header_size
        end = self.header_size + self.sizeofcmds
        if end > len(data):
            raise SigningError("a Mach-O load-command table runs past the file")
        for _ in range(self.ncmds):
            if offset + 8 > end:
                raise SigningError("a Mach-O load command is truncated")
            command = _u32(data, offset, self.little)
            size = _u32(data, offset + 4, self.little)
            if size < 8 or offset + size > end:
                raise SigningError("a Mach-O load command has a bad size")
            self.commands.append((command, size, offset))
            if command == LC_SEGMENT_64:
                name = data[offset + 8:offset + 24].rstrip(b"\x00").decode("latin-1")
                vmaddr = _u64(data, offset + 24, self.little)
                vmsize = _u64(data, offset + 32, self.little)
                fileoff = _u64(data, offset + 40, self.little)
                filesize = _u64(data, offset + 48, self.little)
                count = _u32(data, offset + 64, self.little)
                self.segments[name] = {"vmaddr": vmaddr, "vmsize": vmsize,
                                       "fileoff": fileoff, "filesize": filesize,
                                       "offset": offset, "nsects": count}
                for index in range(count):
                    section = offset + 72 + index * 80
                    if section + 80 > offset + size:
                        break
                    section_offset = _u32(data, section + 48, self.little)
                    if section_offset:
                        self.section_offsets.append(section_offset)
            elif command == LC_CODE_SIGNATURE:
                if size < 16:
                    raise SigningError("LC_CODE_SIGNATURE is malformed")
                dataoff = _u32(data, offset + 8, self.little)
                datasize = _u32(data, offset + 12, self.little)
                self.code_signature = {"dataoff": dataoff, "datasize": datasize,
                                       "offset": offset}
            elif command == LC_UUID and size >= 24:
                self.uuid = data[offset + 8:offset + 24].hex().upper()
            offset += size

    def segment(self, name):
        try:
            return self.segments[name]
        except KeyError:
            raise SigningError("this Mach-O has no %s segment" % name)

    def executable_bounds(self):
        text = self.segment("__TEXT")
        return text["fileoff"], text["fileoff"] + text["filesize"]

    def signature_start(self):
        """Where the code (and the new signature) ends, after dropping the old one."""
        linkedit = self.segment("__LINKEDIT")
        last = max(self.segments.values(), key=lambda seg: seg["fileoff"])
        if last is not linkedit:
            raise SigningError("__LINKEDIT is not the last segment; this binary "
                               "cannot be re-signed safely")
        if self.code_signature:
            dataoff = self.code_signature["dataoff"]
            datasize = self.code_signature["datasize"]
            if dataoff + datasize != len(self.data):
                raise SigningError("the existing signature is not at the end of the "
                                   "binary; this IPA is malformed")
            if dataoff < linkedit["fileoff"]:
                raise SigningError("the existing signature is outside __LINKEDIT")
            if linkedit["fileoff"] + linkedit["filesize"] != len(self.data):
                raise SigningError("__LINKEDIT does not end at the end of the file")
            return dataoff
        if linkedit["fileoff"] + linkedit["filesize"] != len(self.data):
            raise SigningError("the binary has data after __LINKEDIT; it cannot be "
                               "re-signed safely")
        return len(self.data)

    def can_add_command(self):
        first = min(self.section_offsets) if self.section_offsets else None
        if first is None:
            raise SigningError("this binary has no sections, so a signature "
                               "load command cannot be added")
        return first - (self.header_size + self.sizeofcmds) >= 16


def page_digests(data, code_limit):
    return [hashlib.sha256(data[start:start + PAGE_SIZE]).digest()
            for start in range(0, code_limit, PAGE_SIZE)]


def patch_macho(mach, dataoff, datasize, add_command):
    """Return a copy of the binary whose load commands describe the new blob."""
    data = bytearray(mach.data)
    linkedit = mach.segment("__LINKEDIT")

    if add_command:
        if not mach.can_add_command():
            raise SigningError("this Mach-O has no room for a signature load command")
        position = mach.header_size + mach.sizeofcmds
        struct.pack_into("<I" if mach.little else ">I", data, 16, mach.ncmds + 1)
        struct.pack_into("<I" if mach.little else ">I", data, 20, mach.sizeofcmds + 16)
        if mach.little:
            struct.pack_into("<IIII", data, position, LC_CODE_SIGNATURE, 16, dataoff, datasize)
        else:
            struct.pack_into(">IIII", data, position, LC_CODE_SIGNATURE, 16, dataoff, datasize)
    else:
        command = mach.code_signature
        pattern = "<II" if mach.little else ">II"
        struct.pack_into(pattern, data, command["offset"] + 8, dataoff, datasize)

    new_size = (dataoff - linkedit["fileoff"]) + datasize
    rounded = (new_size + 16383) & ~16383
    vmsize = max(linkedit["vmsize"], rounded)
    # Segment64: vmsize at +32, fileoff at +40 (untouched), filesize at +48.
    u64 = "<Q" if mach.little else ">Q"
    struct.pack_into(u64, data, linkedit["offset"] + 32, vmsize)
    struct.pack_into(u64, data, linkedit["offset"] + 48, new_size)
    return data


def _sign_thin(data, identifier, team, entitlements, code_resources, info_plist,
               leaf, chain, key):
    mach = ThinMachO(data)
    if team is None:
        team = ""

    code_end = mach.signature_start()
    dataoff = (code_end + 15) & ~15
    add_command = mach.code_signature is None

    info_blob = None
    resource_blob = None
    entitle_blob = None
    der_blob = None
    if info_plist is not None:
        info_blob = hashlib.sha256(info_plist).digest()
    if code_resources is not None:
        resource_blob = hashlib.sha256(code_resources).digest()
    if entitlements is not None:
        entitle_blob = _blob(CSMAGIC_EMBEDDED_ENTITLEMENTS, entitlements_xml(entitlements))
        der_blob = _blob(CSMAGIC_EMBEDDED_DER_ENTITLEMENTS, der_entitlements(entitlements))

    common_name = ""
    for attribute in leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        common_name = attribute.value
        break
    requirements = build_requirements(identifier, common_name)

    exec_base, exec_limit = mach.executable_bounds()
    exec_flags = 0
    if mach.filetype == MH_EXECUTE:
        exec_flags = CS_EXECSEG_MAIN_BINARY
        if entitlements:
            if entitlements.get("com.apple.private.amfi.can-load-cdhash"):
                exec_flags |= CS_EXECSEG_CAN_LOAD_CDHASH
            if entitlements.get("com.apple.private.amfi.can-execute-cdhash"):
                exec_flags |= CS_EXECSEG_CAN_EXEC_CDHASH

    specials = {CSSLOT_REQUIREMENTS: hashlib.sha256(requirements).digest()}
    if info_blob is not None:
        specials[CSSLOT_INFOSLOT] = info_blob
    if resource_blob is not None:
        specials[CSSLOT_RESOURCEDIR] = resource_blob
    if entitle_blob is not None:
        specials[CSSLOT_ENTITLEMENTS] = hashlib.sha256(entitle_blob).digest()
        specials[CSSLOT_DER_ENTITLEMENTS] = hashlib.sha256(der_blob).digest()

    # The signature length depends on the load commands it describes, which
    # depend on the signature length. Two passes settle it: sizes are stable,
    # only the two integers in the header change.
    predicted = 0
    superblob = b""
    for _attempt in range(4):
        patched = patch_macho(mach, dataoff, predicted, add_command)
        body = bytes(patched[:code_end]) + b"\x00" * (dataoff - code_end)
        hashes = page_digests(body, dataoff)
        code_directory = build_code_directory(identifier, team, dataoff, specials,
                                              hashes, exec_base, exec_limit, exec_flags)
        blobs = [(CSSLOT_CODEDIRECTORY, code_directory),
                 (CSSLOT_REQUIREMENTS, requirements)]
        if entitle_blob is not None:
            blobs.append((CSSLOT_ENTITLEMENTS, entitle_blob))
            blobs.append((CSSLOT_DER_ENTITLEMENTS, der_blob))
        blobs.append((CSSLOT_SIGNATURESLOT, _blob(CSMAGIC_BLOBWRAPPER,
                                                  build_cms(code_directory, leaf, chain, key))))
        superblob = build_superblob(blobs)
        # Apple's signatures end on a 16-byte boundary. The zeros are outside the
        # SuperBlob's own length field and only pad LC_CODE_SIGNATURE.datasize.
        superblob += b"\x00" * ((16 - len(superblob) % 16) % 16)
        if len(superblob) == predicted:
            break
        predicted = len(superblob)
    else:
        raise SigningError("the signature size would not settle for %s" % identifier)

    patched = patch_macho(mach, dataoff, len(superblob), add_command)
    return bytes(patched[:code_end]) + b"\x00" * (dataoff - code_end) + superblob


def _split_fat(data):
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic not in (FAT_MAGIC, FAT_MAGIC_64):
        return None
    if magic == FAT_MAGIC_64:
        raise SigningError("this IPA contains a 64-bit fat binary, which BariSign "
                           "does not rebuild; unpack and repack it as thin arm64 first")
    count = struct.unpack_from(">I", data, 4)[0]
    if count > 16:
        raise SigningError("this fat binary has an implausible slice count")
    slices = []
    for index in range(count):
        base = 8 + index * 20
        cpu, subtype, offset, size, align = struct.unpack_from(">IIIII", data, base)
        if offset + size > len(data):
            raise SigningError("a fat binary slice runs past the file")
        slices.append({"cpu": cpu, "subtype": subtype, "offset": offset,
                       "size": size, "align": align, "data": data[offset:offset + size]})
    return slices


def _join_fat(slices):
    count = len(slices)
    header_size = 8 + count * 20
    for item in slices:
        item["align"] = max(item["align"], 4)               # 16 bytes, at least
    position = header_size
    placements = []
    for item in slices:
        boundary = 1 << item["align"]
        position = (position + boundary - 1) & ~(boundary - 1)
        placements.append((position, item))
        position += len(item["data"])
    out = bytearray(struct.pack(">II", FAT_MAGIC, count))
    for position, item in placements:
        out += struct.pack(">IIIII", item["cpu"], item["subtype"],
                           position, len(item["data"]), item["align"])
    for position, item in placements:
        if len(out) < position:
            out += b"\x00" * (position - len(out))
        out += item["data"]
    return bytes(out)


def sign_macho_bytes(data, identifier, team, entitlements, code_resources,
                     info_plist, leaf, chain, key):
    slices = _split_fat(data)
    if slices is None:
        return _sign_thin(data, identifier, team, entitlements, code_resources,
                          info_plist, leaf, chain, key)
    signed = []
    for item in slices:
        item = dict(item)
        item["data"] = _sign_thin(item["data"], identifier, team, entitlements,
                                  code_resources, info_plist, leaf, chain, key)
        signed.append(item)
    return _join_fat(signed)


# ---------------------------------------------------------------------------
# CodeResources
# ---------------------------------------------------------------------------

def _sha1(data):
    return hashlib.sha1(data).digest()


def _sha256(data):
    return hashlib.sha256(data).digest()


def _is_omitted(relative):
    name = relative.rsplit("/", 1)[-1]
    if name == ".DS_Store":
        return "both"
    if relative.endswith(".lproj/locversion.plist"):
        return "both"
    if relative == "Info.plist":
        return "v2"
    if relative == "PkgInfo":
        return "v2"
    return ""


def build_code_resources(bundle_dir, main_executable):
    """Return the XML Apple's codesign would write for this bundle.

    Both sections are generated because both appear in Apple's output: `files`
    carries SHA-1 digests (older readers), `files2` carries SHA-256. The bundle's
    own executable and its own _CodeSignature are not sealed; everything else is,
    including the contents of nested bundles.
    """
    files = {}
    files2 = {}
    root = os.path.abspath(bundle_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        relative_dir = os.path.relpath(dirpath, root)
        if relative_dir == ".":
            relative_dir = ""
        relative_dir = relative_dir.replace(os.sep, "/")
        if relative_dir.split("/")[0] == "_CodeSignature":
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            relative = (relative_dir + "/" + name) if relative_dir else name
            absolute = os.path.join(dirpath, name)
            if relative == main_executable:
                continue
            omit = _is_omitted(relative)
            if os.path.islink(absolute):
                if omit != "v2":
                    files2[relative] = {"symlink": os.readlink(absolute).replace(os.sep, "/")}
                continue
            try:
                with open(absolute, "rb") as handle:
                    payload = handle.read()
            except OSError as exc:
                raise SigningError("cannot read %s: %s" % (relative, exc))
            if omit != "both":
                files[relative] = _sha1(payload)
            if omit != "v2":
                files2[relative] = {"hash2": _sha256(payload)}
    resources = {"files": files, "files2": files2,
                 "rules": dict(RULES_V1), "rules2": dict(RULES_V2)}
    return plistlib.dumps(resources, fmt=plistlib.FMT_XML, sort_keys=True)


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

BUNDLE_SUFFIXES = (".app", ".appex", ".framework")


def _bundle_info(bundle_dir):
    info_path = os.path.join(bundle_dir, "Info.plist")
    if not os.path.isfile(info_path):
        return None
    try:
        info, raw = read_plist(info_path)
    except SigningError:
        return None
    executable = info.get("CFBundleExecutable")
    if not executable:
        return None
    executable_path = os.path.join(bundle_dir, executable)
    if not os.path.isfile(executable_path):
        raise SigningError("%s says CFBundleExecutable=%s, but that file is missing"
                           % (os.path.basename(bundle_dir), executable))
    return info, raw, executable_path, executable


def _nested_bundles(bundle_dir):
    """Direct nested code bundles; the walk stops at every bundle it finds.

    A framework inside a framework is signed when that framework is signed, so
    this level must not reach into it.
    """
    found = []
    for dirpath, dirnames, _filenames in os.walk(bundle_dir):
        dirnames.sort()
        keep = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if name.endswith(BUNDLE_SUFFIXES) and _bundle_info(full) is not None:
                found.append(full)
            else:
                keep.append(name)
        dirnames[:] = keep
    return found


def _all_bundles(bundle_dir):
    """Every bundle below this one, at every depth (used for bundle-ID rewrites)."""
    found = []
    for child in _nested_bundles(bundle_dir):
        found.append(child)
        found.extend(_all_bundles(child))
    return found


def _inside_nested(path, nested):
    for parent in nested:
        if path == parent or path.startswith(parent + os.sep):
            return True
    return False


def _sign_loose_binaries(bundle_dir, nested, main_executable, config):
    for dirpath, dirnames, filenames in os.walk(bundle_dir):
        dirnames.sort()
        dirnames[:] = [name for name in dirnames
                       if not _inside_nested(os.path.join(dirpath, name), nested)]
        for name in sorted(filenames):
            absolute = os.path.join(dirpath, name)
            relative = os.path.relpath(absolute, bundle_dir).replace(os.sep, "/")
            if relative == main_executable or os.path.islink(absolute):
                continue
            if not looks_like_macho_file(absolute):
                continue
            with open(absolute, "rb") as handle:
                data = handle.read()
            uuid = ThinMachO(data).uuid if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else None
            identifier = name + ("-" + uuid if uuid else "")
            signed = sign_macho_bytes(data, identifier, config["team"], None, None,
                                      None, config["leaf"], config["chain"], config["key"])
            with open(absolute, "wb") as handle:
                handle.write(signed)
            config["binaries"] += 1


def prepare_bundle_ids(app_dir, new_app_id, config):
    """Rewrite CFBundleIdentifier so it matches the App ID in the profile.

    A free account gets a profile for one App ID; iOS matches the bundle on disk
    to it. Apple's own tools do the same when they re-sign to a suffixed App ID.
    """
    info, raw, _exe, _name = _bundle_info(app_dir)
    old_app_id = info.get("CFBundleIdentifier")
    if not old_app_id:
        raise SigningError("the app's Info.plist has no CFBundleIdentifier")

    def rewrite(bundle_dir, target, previous):
        loaded, raw_bytes, _executable_path, _executable = _bundle_info(bundle_dir)
        if not loaded:
            return
        loaded["CFBundleIdentifier"] = target
        tasks = loaded.get("BGTaskSchedulerPermittedIdentifiers")
        if isinstance(tasks, list):
            loaded["BGTaskSchedulerPermittedIdentifiers"] = [
                task.replace(previous, target, 1) if isinstance(task, str) else task
                for task in tasks]
        write_plist(os.path.join(bundle_dir, "Info.plist"), loaded,
                    raw_bytes[:8] == b"bplist00")

    rewrite(app_dir, new_app_id, old_app_id)
    for nested in _all_bundles(app_dir):
        name = os.path.basename(nested)
        if not name.endswith((".appex", ".app")):
            continue
        loaded, _raw, _exe, _name = _bundle_info(nested)
        current = loaded.get("CFBundleIdentifier", "")
        if current.startswith(old_app_id + "."):
            target = new_app_id + current[len(old_app_id):]
        else:
            target = new_app_id + "." + current.rsplit(".", 1)[-1]
        rewrite(nested, target, current)
    return new_app_id


def strip_ungranted_app_groups(app_dir, config):
    """Drop ALTAppGroups entries the profile does not grant.

    AltStore Classic reads ALTAppGroups from Info.plist and, when the key is
    present, assumes an app-group container exists. A free profile has no
    com.apple.security.application-groups entitlement, so
    containerURL(forSecurityApplicationGroupIdentifier:) returns nil and the
    fallback path in the app's app-group migration calls replaceItemAt() with
    the same Application Support/Apps URL as source and destination. That fails
    with EINVAL (22) before the first screen, which is the "The file 'Apps'
    couldn't be saved in the folder 'Application Support'" launch error.

    With the unentitled group removed the same code takes its no-app-group
    branch and stores its data in the app's own container. Groups the profile
    does grant are kept.
    """
    granted = set(config["entitlements"].get("com.apple.security.application-groups") or [])
    for bundle_dir in [app_dir] + _all_bundles(app_dir):
        info = _bundle_info(bundle_dir)
        if not info:
            continue
        loaded, raw_bytes, _executable_path, _executable = info
        groups = loaded.get("ALTAppGroups")
        if not isinstance(groups, list):
            continue
        kept = [group for group in groups if isinstance(group, str) and group in granted]
        if kept == groups:
            continue
        if kept:
            loaded["ALTAppGroups"] = kept
        else:
            loaded.pop("ALTAppGroups", None)
        write_plist(os.path.join(bundle_dir, "Info.plist"), loaded,
                    raw_bytes[:8] == b"bplist00")



def sign_bundle(bundle_dir, config, is_top=False):
    name = os.path.basename(bundle_dir)
    if name.endswith(".app"):
        kind = "app"
    elif name.endswith(".appex"):
        kind = "appex"
    else:
        kind = "framework"

    info, raw_info, executable_path, executable_name = _bundle_info(bundle_dir)
    if info is None:
        raise SigningError("%s is not a code bundle (no usable Info.plist)" % name)

    for dirpath, dirnames, _filenames in os.walk(bundle_dir):
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                raise SigningError(
                    "%s contains a directory symlink (%s); BariSign cannot seal "
                    "this layout yet" % (name, os.path.join(dirpath, name)))

    nested = _nested_bundles(bundle_dir)
    for child in nested:
        sign_bundle(child, config, False)

    _sign_loose_binaries(bundle_dir, nested, executable_name, config)

    if kind in ("app", "appex"):
        profile_path = os.path.join(bundle_dir, "embedded.mobileprovision")
        current = None
        if os.path.isfile(profile_path):
            with open(profile_path, "rb") as handle:
                current = handle.read()
        if current != config["profile_bytes"]:
            with open(profile_path, "wb") as handle:
                handle.write(config["profile_bytes"])
        os.chmod(profile_path, 0o644)

    resources = build_code_resources(bundle_dir, executable_name)
    signature_dir = os.path.join(bundle_dir, "_CodeSignature")
    if not os.path.isdir(signature_dir):
        os.makedirs(signature_dir)
    resources_path = os.path.join(signature_dir, "CodeResources")
    with open(resources_path, "wb") as handle:
        handle.write(resources)
    os.chmod(resources_path, 0o644)

    entitlements = config["entitlements"] if kind in ("app", "appex") else None
    identifier = info.get("CFBundleIdentifier")
    if not identifier:
        identifier = name.rsplit(".", 1)[0]
    with open(executable_path, "rb") as handle:
        data = handle.read()
    if not is_macho(data):
        raise SigningError("%s is not a Mach-O executable" % executable_path)

    signed = sign_macho_bytes(data, identifier, config["team"], entitlements,
                              resources, raw_info, config["leaf"], config["chain"],
                              config["key"])
    mode = os.stat(executable_path).st_mode
    with open(executable_path, "wb") as handle:
        handle.write(signed)
    os.chmod(executable_path, mode & 0o7777)
    config["binaries"] += 1


# ---------------------------------------------------------------------------
# Reading and writing the IPA
# ---------------------------------------------------------------------------

def _safe_path(root, name):
    target = os.path.abspath(os.path.join(root, name))
    if not (target == os.path.abspath(root) or target.startswith(os.path.abspath(root) + os.sep)):
        raise SigningError("the IPA contains an unsafe path: %r" % name[:80])
    return target


def extract_ipa(ipa_path, work_dir):
    try:
        archive = zipfile.ZipFile(ipa_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SigningError("cannot open the IPA: %s" % (exc,))
    with archive:
        for item in archive.infolist():
            name = item.filename
            if not name or name.endswith("/"):
                target = _safe_path(work_dir, name.rstrip("/")) if name.strip("/") else None
                if target:
                    os.makedirs(target, exist_ok=True)
                continue
            target = _safe_path(work_dir, name)
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            mode = (item.external_attr >> 16) & 0xFFFF
            payload = archive.read(item)
            if mode and (mode & 0o170000) == 0o120000:
                link = payload.decode("utf-8", "replace")
                try:
                    os.symlink(link, target)
                except (OSError, NotImplementedError):
                    # a-Shell's sandbox may refuse symlinks; materialise the file
                    source = _safe_path(work_dir, os.path.normpath(
                        os.path.join(os.path.dirname(name), link)))
                    if os.path.isfile(source):
                        shutil.copyfile(source, target)
                    else:
                        with open(target, "wb") as handle:
                            handle.write(payload)
                continue
            with open(target, "wb") as handle:
                handle.write(payload)
            if mode:
                try:
                    os.chmod(target, mode & 0o7777)
                except OSError:
                    pass


def find_payload_app(work_dir):
    payload = os.path.join(work_dir, "Payload")
    if not os.path.isdir(payload):
        raise SigningError("the IPA has no Payload folder")
    apps = sorted(name for name in os.listdir(payload)
                  if name.endswith(".app") and os.path.isdir(os.path.join(payload, name)))
    if not apps:
        raise SigningError("the IPA's Payload folder holds no .app")
    if len(apps) > 1:
        raise SigningError("this IPA has %d apps in Payload; it is not a single-app IPA"
                           % len(apps))
    return os.path.join(payload, apps[0])


def write_ipa(work_dir, output_path):
    temporary = output_path + ".part"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for dirpath, dirnames, filenames in os.walk(work_dir):
                dirnames.sort()
                relative_dir = os.path.relpath(dirpath, work_dir).replace(os.sep, "/")
                if relative_dir == ".":
                    relative_dir = ""
                if relative_dir:
                    info = zipfile.ZipInfo(relative_dir + "/", time.localtime()[:6])
                    info.external_attr = ((0o40755 & 0xFFFF) << 16) | 0x10
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, b"")
                for name in sorted(filenames):
                    absolute = os.path.join(dirpath, name)
                    relative = (relative_dir + "/" + name) if relative_dir else name
                    stat = os.lstat(absolute)
                    if os.path.islink(absolute):
                        target = os.readlink(absolute).encode("utf-8")
                        info = zipfile.ZipInfo(relative, time.localtime(stat.st_mtime)[:6])
                        info.external_attr = ((0o120777 & 0xFFFF) << 16)
                        info.compress_type = zipfile.ZIP_STORED
                        archive.writestr(info, target)
                        continue
                    mode = stat.st_mode & 0o7777
                    info = zipfile.ZipInfo(relative, time.localtime(stat.st_mtime)[:6])
                    info.external_attr = ((0o100000 | mode) & 0xFFFF) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(absolute, "rb") as handle:
                        archive.writestr(info, handle.read())
        os.replace(temporary, output_path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def package_zip(ipa_path, zip_path, progress=None):
    """Archive the signed IPA; the Wi-Fi install does not need the zip."""
    if progress:
        progress("zip")
    temporary = zip_path + ".part"
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            info = zipfile.ZipInfo(os.path.basename(ipa_path), time.localtime()[:6])
            info.external_attr = ((0o100644) & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_STORED
            with open(ipa_path, "rb") as handle:
                archive.writestr(info, handle.read())
        os.replace(temporary, zip_path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Self-check: reopen the finished IPA and verify every byte it claims
# ---------------------------------------------------------------------------

def verify_written_ipa(ipa_path, target_id, config, failures):
    """Read the .ipa back: CRCs, the main signature and the embedded profile.

    The on-disk tree is verified before zipping; this closes the loop on the
    package itself, where a truncated write would otherwise go unnoticed.
    """
    try:
        archive = zipfile.ZipFile(ipa_path)
    except Exception as exc:                                    # noqa: BLE001
        failures.append("the written IPA does not open (%s)" % exc)
        return
    with archive:
        bad = archive.testzip()
        if bad is not None:
            failures.append("the written IPA is corrupt at %s" % bad)
            return
        try:
            app = find_payload_app_in_zip(archive)
            info_bytes = archive.read(app + "Info.plist")
            info = plistlib.loads(info_bytes)
            executable = info.get("CFBundleExecutable")
            if not executable:
                failures.append("the written IPA's Info.plist has no executable")
                return
            if info.get("CFBundleIdentifier") != target_id:
                failures.append("the written bundle id is %r, expected %r"
                                % (info.get("CFBundleIdentifier"), target_id))
            resources = archive.read(app + "_CodeSignature/CodeResources")
            payload = archive.read(app + executable)
            _check_macho_bytes(payload, "written main executable", target_id,
                               config["leaf"], failures, info_bytes, resources,
                               config["entitlements"], config["team"])
            profile = archive.read(app + "embedded.mobileprovision")
            if profile != config["profile_bytes"]:
                failures.append("the written embedded.mobileprovision is not ours")
        except KeyError as exc:
            failures.append("the written IPA is missing %s" % exc)
        except Exception as exc:                                # noqa: BLE001
            failures.append("the written IPA could not be checked (%s)" % exc)


def find_payload_app_in_zip(archive):
    apps = sorted(name for name in archive.namelist()
                  if name.startswith("Payload/") and name.endswith("/")
                  and name.count("/") == 2 and name.split("/")[1].endswith(".app"))
    if len(apps) != 1:
        raise SigningError("the written IPA does not hold exactly one app")
    return apps[0]


def _sig_offsets(mach):
    if not mach.code_signature:
        raise SigningError("no LC_CODE_SIGNATURE")
    return mach.code_signature["dataoff"], mach.code_signature["datasize"]


def _parse_superblob(data, offset):
    magic, length, count = struct.unpack_from(">III", data, offset)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise SigningError("bad superblob magic")
    if offset + length > len(data):
        raise SigningError("the superblob runs past the file")
    found = {}
    for index in range(count):
        slot, relative = struct.unpack_from(">II", data, offset + 12 + index * 8)
        blob_offset = offset + relative
        blob_magic, blob_length = struct.unpack_from(">II", data, blob_offset)
        if blob_offset + blob_length > offset + length:
            raise SigningError("a signature blob runs past the superblob")
        found[slot] = (blob_magic, blob_length, blob_offset)
    return found, offset + length


def _parse_code_directory(data, offset):
    fields = struct.unpack_from(">9I", data, offset)
    magic, length, version, flags, hash_offset, ident_offset, nspecial, ncode, code_limit = fields
    if magic != CSMAGIC_CODEDIRECTORY:
        raise SigningError("bad CodeDirectory magic")
    hash_size = data[offset + 36]
    hash_type = data[offset + 37]
    page_shift = data[offset + 39]
    end = data.index(b"\x00", offset + ident_offset)
    identifier = data[offset + ident_offset:end].decode("utf-8", "replace")
    team_offset = struct.unpack_from(">I", data, offset + 48)[0]
    team = ""
    if team_offset:
        team_end = data.index(b"\x00", offset + team_offset)
        team = data[offset + team_offset:team_end].decode("utf-8", "replace")
    return {
        "length": length, "version": version, "flags": flags,
        "hash_offset": hash_offset, "nspecial": nspecial, "ncode": ncode,
        "code_limit": code_limit, "hash_size": hash_size, "hash_type": hash_type,
        "page_shift": page_shift, "identifier": identifier, "team": team,
        "offset": offset,
    }


def _special_digest(data, cd, slot):
    start = cd["offset"] + cd["hash_offset"] - cd["nspecial"] * cd["hash_size"]
    index = cd["nspecial"] - slot
    if index < 0 or slot < 1:
        return b"\x00" * cd["hash_size"]
    return data[start + index * cd["hash_size"]:start + (index + 1) * cd["hash_size"]]


def _cms_check(der_bytes, cd_bytes, leaf, failures, where):
    try:
        outer_tag, outer, _ = der_read(der_bytes, 0)
        if outer_tag != 0x30:
            raise SigningError("CMS is not a sequence")
        oid_tag, oid_value, position = der_read(outer, 0)
        if der_oid(OID_SIGNED_DATA) != der(oid_tag, oid_value):
            raise SigningError("CMS is not SignedData")
        tag, signed_data, _ = der_read(outer, position)
        if tag != 0xA0:
            raise SigningError("CMS SignedData is not [0]")
        tag, body, _ = der_read(signed_data, 0)
        items = der_children(body)
        certificates = []
        signer = None
        for item_tag, item_value in items:
            if item_tag == 0xA0:
                offset = 0
                while offset < len(item_value):
                    cert_tag, cert_value, offset = der_read(item_value, offset)
                    certificates.append(x509.load_der_x509_certificate(
                        der(cert_tag, cert_value)))
            elif item_tag == 0x31:
                signer = item_value
        if signer is None:
            raise SigningError("CMS has no signer")
        _tag, signer_body, _ = der_read(signer, 0)
        signer_items = der_children(signer_body)
        attrs_raw = None
        signature = None
        for tag, value in signer_items:
            if tag == 0xA0:
                attrs_raw = value
            elif tag == 0x04:
                signature = value
        if attrs_raw is None or signature is None:
            raise SigningError("CMS signer is incomplete")

        digest = hashlib.sha256(cd_bytes).digest()
        wanted = {OID_MESSAGE_DIGEST: None, OID_CDHASHES: None, OID_CDHASHES_PLIST: None}
        for tag, value in der_children(attrs_raw):
            if tag != 0x30:
                raise SigningError("a CMS attribute is malformed")
            parts = der_children(value)
            if len(parts) < 2:
                raise SigningError("a CMS attribute has no value")
            for name in list(wanted):
                if der(0x06, parts[0][1]) == der_oid(name):
                    wanted[name] = parts[1][1]

        if wanted[OID_MESSAGE_DIGEST] is None:
            raise SigningError("CMS has no messageDigest")
        tag, value, _ = der_read(wanted[OID_MESSAGE_DIGEST], 0)
        if tag != 0x04 or value != digest:
            raise SigningError("CMS messageDigest does not match the CodeDirectory")

        if wanted[OID_CDHASHES] is None:
            raise SigningError("CMS has no 9.2 attribute")
        tag, value, _ = der_read(wanted[OID_CDHASHES], 0)
        if tag != 0x30:
            raise SigningError("CMS 9.2 attribute is not a sequence")
        pairs = der_children(value)
        if len(pairs) < 2 or pairs[1][0] != 0x04 or pairs[1][1] != digest:
            raise SigningError("CMS 9.2 digest does not match the CodeDirectory")

        if wanted[OID_CDHASHES_PLIST] is None:
            raise SigningError("CMS has no 9.1 attribute")
        tag, value, _ = der_read(wanted[OID_CDHASHES_PLIST], 0)
        if tag != 0x04:
            raise SigningError("CMS 9.1 attribute is not an octet string")
        try:
            if not value.startswith(b"bplist00") and b"<?xml" not in value[:64]:
                value = b'<?xml version="1.0" encoding="UTF-8"?>\n' + value
            plist = plistlib.loads(value)
        except Exception:                                       # noqa: BLE001
            raise SigningError("CMS 9.1 is not a plist")
        if plist.get("cdhashes", [None])[0] != digest[:20]:
            raise SigningError("CMS 9.1 cdhash does not match the CodeDirectory")

        signed_attrs = der(0x31, attrs_raw)
        signer_cert = None
        for cert in certificates:
            if cert.public_bytes(serialization.Encoding.DER) == \
                    leaf.public_bytes(serialization.Encoding.DER):
                signer_cert = cert
                break
        if signer_cert is None:
            raise SigningError("the CMS does not carry the expected certificate")
        signer_cert.public_key().verify(signature, signed_attrs,
                                        padding.PKCS1v15(), hashes.SHA256())

        # Walk the chain using only what the CMS carries.
        current = signer_cert
        for _step in range(3):
            if current.subject == current.issuer:
                break
            parent = None
            for candidate in certificates:
                if candidate.subject != current.issuer:
                    continue
                try:
                    _verify_with(candidate.public_key(), current.signature,
                                 current.tbs_certificate_bytes,
                                 current.signature_hash_algorithm)
                except Exception:                               # noqa: BLE001
                    continue
                parent = candidate
                break
            if parent is None:
                raise SigningError("the CMS certificate chain is broken at %s"
                                   % current.issuer.rfc4514_string()[:60])
            current = parent
    except SigningError as exc:
        failures.append("%s: %s" % (where, exc))
    except Exception as exc:                                    # noqa: BLE001
        failures.append("%s: CMS check failed (%s)" % (where, exc))


def _check_macho(path, relative, expected_ident, leaf, failures,
                 expected_info=None, expected_resources=None,
                 expected_entitlements=None, expect_team=None):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        failures.append("%s: cannot read (%s)" % (relative, exc))
        return
    _check_macho_bytes(data, relative, expected_ident, leaf, failures,
                       expected_info, expected_resources, expected_entitlements,
                       expect_team)


def _check_macho_bytes(data, relative, expected_ident, leaf, failures,
                       expected_info=None, expected_resources=None,
                       expected_entitlements=None, expect_team=None):
    try:
        if not is_macho(data):
            return
        slices = _split_fat(data)
        if slices:
            parts = [item["data"] for item in slices]
        else:
            parts = [data]
        for part in parts:
            mach = ThinMachO(part)
            dataoff, datasize = _sig_offsets(mach)
            if dataoff + datasize != len(part):
                raise SigningError("the signature does not end at end of file")
            if mach.signature_start() != dataoff:
                raise SigningError("the load commands disagree with where the "
                                   "signature actually starts")
            blobs, _end = _parse_superblob(part, dataoff)
            if CSSLOT_CODEDIRECTORY not in blobs:
                raise SigningError("no CodeDirectory")
            _magic, _length, cd_offset = blobs[CSSLOT_CODEDIRECTORY]
            cd_blob = part[cd_offset:cd_offset + _length]
            cd = _parse_code_directory(part, cd_offset)
            if cd["version"] != 0x20400:
                raise SigningError("unexpected CodeDirectory version %#x" % cd["version"])
            if cd["hash_type"] != 2 or cd["hash_size"] != 32:
                raise SigningError("the CodeDirectory is not SHA-256")
            if cd["page_shift"] != PAGE_SHIFT:
                raise SigningError("the CodeDirectory page size is not %d KB"
                                   % (PAGE_SIZE // 1024))
            if cd["code_limit"] != dataoff:
                raise SigningError("codeLimit does not point at the signature")
            hashes = page_digests(part[:dataoff], dataoff)
            start = cd_offset + cd["hash_offset"]
            for index, digest in enumerate(hashes):
                stored = part[start + index * 32:start + (index + 1) * 32]
                if stored != digest:
                    raise SigningError("page %d does not hash correctly" % index)
            if expected_ident is not None and cd["identifier"] != expected_ident:
                raise SigningError("identifier is %r, expected %r"
                                   % (cd["identifier"], expected_ident))
            if expect_team and cd["team"] != expect_team:
                raise SigningError("team is %r, expected %r" % (cd["team"], expect_team))
            if expected_info is not None:
                if _special_digest(part, cd, CSSLOT_INFOSLOT) != hashlib.sha256(expected_info).digest():
                    raise SigningError("the Info.plist slot does not match")
            if expected_resources is not None:
                if _special_digest(part, cd, CSSLOT_RESOURCEDIR) != hashlib.sha256(expected_resources).digest():
                    raise SigningError("the CodeResources slot does not match")
            if expected_entitlements is not None:
                xml = entitlements_xml(expected_entitlements)
                der_bundle = der_entitlements(expected_entitlements)
                xml_blob = _blob(CSMAGIC_EMBEDDED_ENTITLEMENTS, xml)
                der_body = _blob(CSMAGIC_EMBEDDED_DER_ENTITLEMENTS, der_bundle)
                if _special_digest(part, cd, CSSLOT_ENTITLEMENTS) != hashlib.sha256(xml_blob).digest():
                    raise SigningError("the entitlements slot does not match")
                if _special_digest(part, cd, CSSLOT_DER_ENTITLEMENTS) != hashlib.sha256(der_body).digest():
                    raise SigningError("the DER entitlements slot does not match")
                slot = blobs.get(CSSLOT_ENTITLEMENTS)
                if not slot:
                    raise SigningError("no entitlements blob")
                _m, length, offset = slot
                got = plistlib.loads(part[offset + 8:offset + length])
                if got != expected_entitlements:
                    raise SigningError("the entitlements blob differs from the profile")
            if CSSLOT_SIGNATURESLOT not in blobs:
                raise SigningError("no CMS signature")
            _magic, length, offset = blobs[CSSLOT_SIGNATURESLOT]
            _cms_check(part[offset + 8:offset + length], cd_blob, leaf, failures, relative)
    except SigningError as exc:
        failures.append("%s: %s" % (relative, exc))
    except Exception as exc:                                    # noqa: BLE001
        failures.append("%s: %s" % (relative, exc))


def _check_code_resources(bundle_dir, main_executable, failures, relative_dir):
    resources_path = os.path.join(bundle_dir, "_CodeSignature", "CodeResources")
    if not os.path.isfile(resources_path):
        failures.append("%s: no _CodeSignature/CodeResources" % relative_dir)
        return
    try:
        with open(resources_path, "rb") as handle:
            raw = handle.read()
        resources = plistlib.loads(raw)
    except Exception as exc:                                    # noqa: BLE001
        failures.append("%s: CodeResources does not parse (%s)" % (relative_dir, exc))
        return
    for relative, digest in (resources.get("files") or {}).items():
        absolute = os.path.join(bundle_dir, relative)
        if not os.path.isfile(absolute):
            failures.append("%s: sealed file is missing: %s" % (relative_dir, relative))
            continue
        with open(absolute, "rb") as handle:
            if hashlib.sha1(handle.read()).digest() != digest:
                failures.append("%s: SHA-1 mismatch: %s" % (relative_dir, relative))
    for relative, entry in (resources.get("files2") or {}).items():
        absolute = os.path.join(bundle_dir, relative)
        if "symlink" in entry:
            if not os.path.islink(absolute) or os.readlink(absolute) != entry["symlink"]:
                failures.append("%s: symlink mismatch: %s" % (relative_dir, relative))
            continue
        if not os.path.isfile(absolute):
            failures.append("%s: sealed file is missing: %s" % (relative_dir, relative))
            continue
        with open(absolute, "rb") as handle:
            if hashlib.sha256(handle.read()).digest() != entry.get("hash2"):
                failures.append("%s: SHA-256 mismatch: %s" % (relative_dir, relative))
    if main_executable in (resources.get("files2") or {}):
        failures.append("%s: the bundle's own executable is sealed" % relative_dir)


def verify_signed_app(app_dir, config, failures):
    """Independent second pass over what was written: hashes, slots and CMS."""
    verify_bundle(app_dir, config, failures, is_top=True)


def verify_bundle(bundle_dir, config, failures, is_top=False):
    relative_dir = os.path.relpath(bundle_dir, config["work_dir"]).replace(os.sep, "/")
    info, raw_info, executable_path, executable_name = _bundle_info(bundle_dir)
    if info is None:
        failures.append("%s: no usable Info.plist" % relative_dir)
        return
    _check_code_resources(bundle_dir, executable_name, failures, relative_dir)

    resources_path = os.path.join(bundle_dir, "_CodeSignature", "CodeResources")
    resources = None
    if os.path.isfile(resources_path):
        with open(resources_path, "rb") as handle:
            resources = handle.read()

    entitlements = config["entitlements"] if os.path.basename(bundle_dir).endswith(
        (".app", ".appex")) else None
    _check_macho(executable_path, relative_dir + "/" + executable_name,
                 info.get("CFBundleIdentifier"), config["leaf"], failures,
                 raw_info, resources, entitlements, config["team"])

    if os.path.basename(bundle_dir).endswith((".app", ".appex")):
        profile_path = os.path.join(bundle_dir, "embedded.mobileprovision")
        if not os.path.isfile(profile_path):
            failures.append("%s: no embedded.mobileprovision" % relative_dir)
        else:
            with open(profile_path, "rb") as handle:
                if handle.read() != config["profile_bytes"]:
                    failures.append("%s: embedded.mobileprovision is not the profile "
                                    "we were given" % relative_dir)

    nested = _nested_bundles(bundle_dir)
    for child in nested:
        verify_bundle(child, config, failures)
    # Loose Mach-O files must carry a signature too.
    for dirpath, dirnames, filenames in os.walk(bundle_dir):
        dirnames.sort()
        dirnames[:] = [name for name in dirnames
                       if not _inside_nested(os.path.join(dirpath, name), nested)]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            if os.path.islink(absolute) or not looks_like_macho_file(absolute):
                continue
            if os.path.abspath(absolute) == os.path.abspath(executable_path):
                continue
            _check_macho(absolute, os.path.relpath(absolute, config["work_dir"]),
                         None, config["leaf"], failures)


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------

def find_saved_paths(state_dir=None):
    """Locate the key, certificate and profile the portal stage saved."""
    state_dir = state_dir or STATE_DIR
    import json
    state = {}
    state_file = os.path.join(state_dir, "barisign.json")
    if os.path.isfile(state_file):
        try:
            with open(state_file) as handle:
                state = json.load(handle)
        except Exception:                                       # noqa: BLE001
            state = {}

    def pick(key, default_name, pattern=None):
        saved = state.get(key)
        if saved and os.path.isfile(saved):
            return saved
        candidate = os.path.join(state_dir, default_name)
        if os.path.isfile(candidate):
            return candidate
        if pattern:
            matches = []
            if os.path.isdir(state_dir):
                for name in sorted(os.listdir(state_dir)):
                    candidate = os.path.join(state_dir, name, default_name)
                    if os.path.isfile(candidate):
                        matches.append(candidate)
            if len(matches) == 1:
                return matches[0]
        return ""

    key = pick("key", "key.pem", pattern=True)
    cert = pick("cert", "cert.pem")
    profile = pick("profile", "profile.mobileprovision")
    return {"key": key, "cert": cert, "profile": profile,
            "email": state.get("email", ""), "udid": state.get("udid", "")}


def _prepare_config(profile_path, key_path, cert_path, udid=None):
    profile = read_mobileprovision(profile_path)
    with open(profile_path, "rb") as handle:
        profile_bytes = handle.read()

    entitlements = profile.get("Entitlements")
    if not isinstance(entitlements, dict):
        raise SigningError("the profile has no Entitlements dictionary")
    teams = profile.get("TeamIdentifier") or []
    if not teams:
        raise SigningError("the profile names no team")
    team = teams[0]

    application_id = entitlements.get("application-identifier")
    if not application_id:
        raise SigningError("the profile has no application-identifier")
    if application_id == team + ".*":
        app_id = "*"
    elif application_id.startswith(team + "."):
        app_id = application_id[len(team) + 1:]
    else:
        raise SigningError("this profile's application-identifier (%s) does not "
                           "belong to team %s; run portal again"
                           % (str(application_id)[:60], team))

    expires = profile.get("ExpirationDate")
    if isinstance(expires, datetime.datetime):
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        if expires < datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None):
            raise SigningError("this provisioning profile expired on %s" % expires)

    devices = profile.get("ProvisionedDevices") or []
    if devices and udid:
        normal = lambda value: "".join(ch for ch in (value or "").lower()
                                       if ch in "0123456789abcdef")
        if not any(normal(device) == normal(udid) for device in devices):
            raise SigningError("this profile does not include this device (%s); "
                               "run portal again" % udid)

    leaf = load_certificate(cert_path)
    key = load_private_key(key_path)

    # A certificate that expired (or is not valid yet) makes installd reject the
    # app with a generic "cannot be installed"; catch it here, where the message
    # can say why. Revocation is the one thing that cannot be checked offline:
    # only Apple knows, so `portal` must be run after a revoke.
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        not_after = leaf.not_valid_after_utc
        not_before = leaf.not_valid_before_utc
    except AttributeError:                  # cryptography < 42
        not_after = leaf.not_valid_after
        not_before = leaf.not_valid_before
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=datetime.timezone.utc)
    if not_before.tzinfo is None:
        not_before = not_before.replace(tzinfo=datetime.timezone.utc)
    if not_after < now:
        raise SigningError("the signing certificate expired on %s; run portal again"
                           % not_after.date())
    if not_before > now:
        raise SigningError("the signing certificate is not valid until %s"
                           % not_before.date())
    units = leaf.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    if units and units[0].value != team:
        raise SigningError("the certificate belongs to team %s, the profile to %s"
                           % (units[0].value, team))
    numbers = getattr(key, "private_numbers", lambda: None)()
    public = getattr(numbers, "public_numbers", None)
    cert_public = leaf.public_key()
    if not isinstance(cert_public, rsa.RSAPublicKey) or not isinstance(key, rsa.RSAPrivateKey):
        raise SigningError("the signing key and certificate must both be RSA")
    if public is None or public.n != cert_public.public_numbers().n:
        raise SigningError("key.pem and cert.pem are not a pair; run portal again")

    profile_certs = profile.get("DeveloperCertificates") or []
    leaf_der = leaf.public_bytes(serialization.Encoding.DER)
    if profile_certs and leaf_der not in [bytes(item) for item in profile_certs]:
        raise SigningError("cert.pem is not the certificate this profile was issued "
                           "for; run portal again")

    chain = certificate_chain(leaf)

    entitlements = dict(entitlements)
    entitlements["com.apple.developer.team-identifier"] = team
    if app_id == "*":
        app_id = None                          # wildcard: keep the IPA's own id
    return {
        "team": team, "app_id": app_id, "entitlements": entitlements,
        "profile_bytes": profile_bytes, "profile": profile,
        "leaf": leaf, "chain": chain, "key": key, "binaries": 0,
    }


def sign_ipa(ipa_path, profile_path, key_path, cert_path, output_ipa=None,
             output_zip=None, udid=None, progress=None, make_zip=True,
             drop_extensions=False):
    """Sign one IPA. Returns a summary dictionary. Raises SigningError."""
    progress = progress or _say
    for label, path in (("IPA", ipa_path), ("profile", profile_path),
                        ("key", key_path), ("certificate", cert_path)):
        if not path or not os.path.isfile(path):
            raise SigningError("%s not found: %s" % (label, path or "(no path)"))

    ipa_path = os.path.abspath(ipa_path)
    base = ipa_path[:-4] if ipa_path.lower().endswith(".ipa") else ipa_path
    output_ipa = os.path.abspath(output_ipa or (base + "-signed.ipa"))
    if not output_zip:
        zip_base = output_ipa[:-4] if output_ipa.lower().endswith(".ipa") else output_ipa
        output_zip = zip_base + ".zip"
    output_zip = os.path.abspath(output_zip)

    progress("read")
    config = _prepare_config(profile_path, key_path, cert_path, udid)

    parent = os.path.dirname(os.path.abspath(output_ipa)) or "."
    if not os.path.isdir(parent):
        raise SigningError("the output folder does not exist: %s" % parent)
    if os.path.abspath(output_ipa) == ipa_path:
        raise SigningError("the output would overwrite the IPA being signed")
    work_dir = tempfile.mkdtemp(prefix=".barisign-sign-", dir=parent)
    config["work_dir"] = work_dir
    try:
        progress("open")
        extract_ipa(ipa_path, work_dir)
        app_dir = find_payload_app(work_dir)
        if drop_extensions:
            # Diagnostic / fallback: an app extension needs an App ID and a
            # profile of its own. Without one, installd refuses the whole app
            # ("cannot be installed"). Dropping PlugIns proves whether that is
            # what is happening; the widget is lost, the app is not.
            plugins = os.path.join(app_dir, "PlugIns")
            if os.path.isdir(plugins):
                shutil.rmtree(plugins)
        app_info, _raw, _exe, _name = _bundle_info(app_dir)
        if app_info is None:
            raise SigningError("the app has no usable Info.plist")
        original_id = app_info.get("CFBundleIdentifier") or ""
        target_id = config["app_id"] or original_id
        if not target_id:
            raise SigningError("the app has no CFBundleIdentifier")

        progress("prepare")
        if config["app_id"]:
            prepare_bundle_ids(app_dir, target_id, config)
        strip_ungranted_app_groups(app_dir, config)
        # The profile's entitlement must name the bundle that is now on disk.
        config["entitlements"]["application-identifier"] = \
            config["team"] + "." + target_id
        config["bundle_id"] = target_id

        progress("sign")
        sign_bundle(app_dir, config, True)

        progress("check")
        failures = []
        verify_signed_app(app_dir, config, failures)
        if failures:
            raise SigningError("the self-check failed:\n    " + "\n    ".join(failures[:6]))

        progress("write")
        write_ipa(work_dir, output_ipa)
        verify_written_ipa(output_ipa, target_id, config, failures)
        if failures:
            raise SigningError("the written IPA failed its check:\n    "
                               + "\n    ".join(failures[:6]))

        zip_path = ""
        if make_zip:
            zip_path = output_zip
            package_zip(output_ipa, zip_path, progress)

        progress("done")
        return {
            "ipa": output_ipa,
            "zip": zip_path,
            "bundle_id": target_id,
            "team": config["team"],
            "binaries": config["binaries"],
            "size": os.path.getsize(output_ipa),
        }
    finally:
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Stand-alone command line (for testing in a codespace; the real entry point is
# sh bin/barisign sign)
# ---------------------------------------------------------------------------

def _cli(argv):
    def value(flag):
        if flag in argv:
            index = argv.index(flag) + 1
            if index < len(argv):
                return argv[index]
        return ""

    ipa = value("--ipa") or (argv[0] if argv and not argv[0].startswith("-") else "")
    if not ipa:
        print("usage: python3 -u poc/signer.py --ipa X.ipa "
              "[--profile P --key K --cert C] [--out O] [--no-zip]")
        return 2
    saved = find_saved_paths()
    profile = value("--profile") or saved["profile"]
    key = value("--key") or saved["key"]
    cert = value("--cert") or saved["cert"]
    if not (profile and key and cert):
        print("! the portal files are missing; run: sh bin/barisign portal")
        return 2

    def report(label):
        sys.stdout.write("\r\033[2K  %s ...." % label)
        sys.stdout.flush()

    try:
        result = sign_ipa(ipa, profile, key, cert, output_ipa=value("--out") or None,
                          udid=saved.get("udid") or None, progress=report,
                          make_zip="--no-zip" not in argv,
                          drop_extensions="--no-extensions" in argv)
    except SigningError as exc:
        sys.stdout.write("\r\033[2K")
        print("! %s" % exc)
        return 1
    sys.stdout.write("\r\033[2K")
    print("SIGN  ok  %s" % os.path.basename(result["ipa"]))
    print("  ipa   %s" % result["ipa"])
    if result["zip"]:
        print("  zip   %s" % result["zip"])
    print("  team  %s  bundle %s" % (result["team"], result["bundle_id"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_cli(sys.argv[1:]))
    except KeyboardInterrupt:
        print()
        sys.exit(130)
    except Exception:                                           # noqa: BLE001
        print()
        print("UNEXPECTED ERROR:")
        traceback.print_exc()
        sys.exit(4)
