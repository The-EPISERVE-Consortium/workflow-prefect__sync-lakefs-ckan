"""OCI registry helpers."""

import requests

_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
])


def get_image_created(image: str, tag: str) -> str:
    """
    Fetch the creation timestamp of a public OCI image from its registry.

    Supports ghcr.io. Returns an ISO 8601 string, or '' on any failure
    (missing image, network error, unsupported registry).
    """
    if not image or not tag:
        return ""
    if not image.startswith("ghcr.io/"):
        return ""

    registry = "ghcr.io"
    repo     = image[len("ghcr.io/"):]

    try:
        token_r = requests.get(
            f"https://{registry}/token",
            params={"scope": f"repository:{repo}:pull", "service": registry},
            timeout=10,
        )
        token   = token_r.json()["token"]
        headers = {"Authorization": f"Bearer {token}", "Accept": _ACCEPT}

        manifest_r = requests.get(
            f"https://{registry}/v2/{repo}/manifests/{tag}",
            headers=headers,
            timeout=10,
        )
        manifest = manifest_r.json()

        # Manifest list (multi-arch): resolve to linux/amd64, or first entry.
        if "manifests" in manifest:
            chosen = next(
                (m for m in manifest["manifests"]
                 if m.get("platform", {}).get("os") == "linux"
                 and m.get("platform", {}).get("architecture") == "amd64"),
                manifest["manifests"][0],
            )
            manifest_r = requests.get(
                f"https://{registry}/v2/{repo}/manifests/{chosen['digest']}",
                headers=headers,
                timeout=10,
            )
            manifest = manifest_r.json()

        config_digest = manifest["config"]["digest"]
        config_r = requests.get(
            f"https://{registry}/v2/{repo}/blobs/{config_digest}",
            headers=headers,
            timeout=10,
        )
        return config_r.json().get("created", "")
    except Exception:
        return ""
