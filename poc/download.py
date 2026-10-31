#!/usr/bin/env python3
# =============================================================================
#  BariSign — variant resolution and IPA download (MIT)
#
#  Resolves a variant from variants.json against the project's own GitHub
#  releases, at runtime, and downloads the .ipa while checking the size and the
#  SHA-256 digest GitHub publishes for the asset. Nothing is hardcoded: versions
#  are read from the API on every run, and prerelease tags are refused.
#
#  Rules (from variants.json):
#    * stable  — newest release whose tag has no alpha/beta/rc, with a matching
#                .ipa asset. Never /releases/latest: SideStore's is an alpha.
#    * nightly — the release whose tag is exactly "nightly"; its published_at is
#                stale, so the asset's updated_at is what we show.
#    * AltStore Classic is a single release whose asset name carries the version;
#                assets are matched by pattern, never by a fixed filename.
#
#  Everything runs in a-Shell: stdlib plus the vendored sign-in stack's HTTP
#  helper and its TLS context (a-Shell's Python ships an empty trust store, so the
#  shipped CA bundle is always loaded explicitly).
#
#  STATUS: exercised on the signer; the download and signing flow has run on
#          iOS 27 and the signed IPA installed on a device. See docs/RESULTS.md.
# =============================================================================

import hashlib
import http.client
import json
import os
import re
import urllib.parse

import signin

VERSION = "0.1.0"

RELEASES_URL = "https://api.github.com/repos/%s/releases?per_page=30"
USER_AGENT = "BariSign (+https://github.com/barismd/barisign)"
CHUNK = 262144
BAD_TAG = re.compile(r"(alpha|beta|rc)", re.IGNORECASE)


class DownloadError(Exception):
    """Anything the user can act on: no network, rate limit, no asset, bad file."""


def _headers():
    return {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}


def _api(url, timeout=40):
    try:
        status, headers, body = signin.request("GET", url, headers=_headers(),
                                               timeout=timeout)
    except Exception as exc:                                    # noqa: BLE001
        raise DownloadError("no connection to GitHub (%s)" % (str(exc)[:90],))
    text = (body or b"").decode("utf-8", "replace")
    if status == 403 and "rate limit" in text.lower():
        raise DownloadError(
            "GitHub's API rate limit is used up for now. Wait an hour, or download\n"
            "    the IPA yourself and run: sh bin/barisign sign --ipa FILE")
    if status == 404:
        raise DownloadError("GitHub does not know this release page")
    if status != 200:
        raise DownloadError("GitHub answered HTTP %d" % status)
    try:
        return json.loads(text)
    except ValueError:
        raise DownloadError("GitHub sent something that is not JSON")


def _asset_for(release, pattern):
    """The first .ipa asset matching the variant's pattern."""
    exact = pattern and pattern.lower() != ".ipa"
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if not name.lower().endswith(".ipa"):
            continue
        if exact:
            if name == pattern:
                return asset
        else:
            return asset
    return None


def _published(release):
    return release.get("published_at") or ""


def resolve(variant):
    """Ask GitHub what this variant's current .ipa is.

    Returns a dictionary with url, size, sha256, version and the release name.
    Raises DownloadError with a message that says what to do next.
    """
    repo = variant.get("repo")
    if not repo:
        raise DownloadError("%s has no release source in variants.json"
                            % variant.get("name", "this variant"))
    releases = _api(RELEASES_URL % repo)
    if not isinstance(releases, list):
        raise DownloadError("GitHub returned an unexpected release list")

    channel = variant.get("channel") or "stable"
    eligible = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name") or ""
        if channel == "nightly":
            if tag != "nightly":
                continue
        else:
            if release.get("prerelease") or BAD_TAG.search(tag):
                continue
        eligible.append(release)
    eligible.sort(key=_published, reverse=True)

    for release in eligible:
        asset = _asset_for(release, variant.get("asset_pattern"))
        if asset is None:
            continue
        digest = asset.get("digest") or ""
        sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
        if channel == "nightly":
            stamp = (asset.get("updated_at") or "")[:10]
            version = "%s nightly %s" % (variant.get("name") or repo, stamp or "?")
        else:
            version = "%s %s" % (variant.get("name") or repo, release.get("tag_name"))
        return {
            "id": variant.get("id") or "",
            "name": variant.get("name") or repo,
            "channel": channel,
            "version": version,
            "tag": release.get("tag_name") or "",
            "url": asset.get("browser_download_url") or "",
            "size": int(asset.get("size") or 0),
            "sha256": sha256,
            "warning": variant.get("warning") or "",
        }
    raise DownloadError("%s has no published .ipa for the %s channel right now"
                        % (variant.get("name", repo), channel))


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _open_stream(url, timeout=90, redirects=6):
    """GET a URL and hand back a response we can stream from disk to disk."""
    while True:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise DownloadError("refusing a non-https download: %s" % url[:80])
        host = parts.hostname
        if not host:
            raise DownloadError("that download URL has no host")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        context = signin._ctx_for(host)                          # noqa: SLF001
        connection = http.client.HTTPSConnection(host, parts.port or 443,
                                                 timeout=timeout, context=context)
        try:
            connection.request("GET", path, headers={"User-Agent": USER_AGENT,
                                                     "Accept-Encoding": "identity"})
            response = connection.getresponse()
        except Exception as exc:                                # noqa: BLE001
            connection.close()
            raise DownloadError("download failed (%s)" % (str(exc)[:90],))
        if response.status in (301, 302, 303, 307, 308) and redirects:
            location = response.getheader("location")
            response.read()
            connection.close()
            if not location:
                raise DownloadError("GitHub redirected without a target")
            url = urllib.parse.urljoin(url, location)
            redirects -= 1
            continue
        if response.status != 200:
            body = response.read(200).decode("utf-8", "replace")
            connection.close()
            raise DownloadError("download failed: HTTP %d %s"
                                % (response.status, " ".join(body.split())[:80]))
        return connection, response


def _download(url, destination, info, progress=None):
    partial = destination + ".part"
    digest = hashlib.sha256()
    received = 0
    total = info.get("size") or 0
    try:
        connection, response = _open_stream(url)
        try:
            with open(partial, "wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    received += len(block)
                    if progress:
                        progress(received, total)
        finally:
            connection.close()
    except DownloadError:
        _remove(partial)
        raise
    except Exception as exc:                                    # noqa: BLE001
        _remove(partial)
        raise DownloadError("download broke off (%s)" % (str(exc)[:90],))

    if total and received != total:
        _remove(partial)
        raise DownloadError("the download is %d bytes, GitHub said %d"
                            % (received, total))
    if info.get("sha256") and digest.hexdigest() != info["sha256"]:
        _remove(partial)
        raise DownloadError("the download's SHA-256 does not match GitHub's; "
                            "nothing was kept")
    os.replace(partial, destination)
    try:
        os.chmod(destination, 0o644)
    except OSError:
        pass


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def ensure(variant, folder, progress=None):
    """Make sure this variant's current IPA is on disk; download only if needed.

    A file whose size and SHA-256 already match is reused, so a second run of the
    guided flow does not pull 30 MB again.
    """
    info = resolve(variant)
    if not info["url"]:
        raise DownloadError("GitHub did not give a download URL for %s" % info["version"])
    name = (variant.get("id") or "barisign") + ".ipa"
    destination = os.path.join(folder, name)

    if os.path.isfile(destination):
        try:
            same_size = os.path.getsize(destination) == info["size"]
        except OSError:
            same_size = False
        if same_size and (not info["sha256"]
                          or _sha256_file(destination) == info["sha256"]):
            info = dict(info)
            info.update({"path": destination, "reused": True})
            return info

    if not os.path.isdir(folder):
        os.makedirs(folder)
    _download(info["url"], destination, info, progress)
    info = dict(info)
    info.update({"path": destination, "reused": False})
    return info


def variant_by_id(variants, identifier):
    for variant in variants:
        if variant.get("id") == identifier:
            return variant
    return None
