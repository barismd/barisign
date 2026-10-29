# Anisette servers — what they are, what they see, and which ones we use

Apple will not answer a sign-in unless the request carries **device-identity headers** (a machine
identifier plus one-time codes). Those can only be produced by Apple's `ADI` library, which runs on real
Apple platforms. An **anisette server** runs that library and mints the headers for you.

## What the server sees — and what it never sees

| Sees | Never sees |
|---|---|
| your IP address | your Apple ID |
| the ADI provisioning exchange (a device-identity blob) | your password |
| the one-time `X-Apple-I-MD*` headers it just minted | the SRP proof derived from the password |
| which OS family you claim (Mac / Android / Windows) | your session tokens, UDID, or the apps you install |

The password becomes an SRP proof **on your device**; that proof, and everything after it, goes straight
to `gsa.apple.com` from your device. BariSign additionally refuses to send an authenticated Apple request
to any host that is not `*.apple.com` (spoofed URL bag protection), so the anisette server is never in
that path.

**Worst case with a malicious operator:** they can log IPs, and they could reuse the machine identity
*they* provisioned — which grants no access to your account. For that reason, and because sideloading
burns certificate slots, a throwaway Apple ID is a good idea anyway.

## Which servers BariSign uses

The list is [SideStore/anisette-servers](https://github.com/SideStore/anisette-servers) — **AGPL-3.0**,
maintained by the SideStore team, with a `kind` field saying what each server emulates. The server
software itself is open source: [SideStore/omnisette-server](https://github.com/SideStore/omnisette-server)
and [Dadoum/anisette-v3-server](https://github.com/Dadoum/anisette-v3-server).

BariSign's default order (checked live 2026-10-29):

| # | Server | Operator | Emulates | Why |
|---|---|---|---|---|
| 1 | `mac-ani.jkcoxson.com` | jkcoxson | macOS | the server the 2026-10-24 `-80046`/503 fixes use |
| 2 | `ani.jkcoxson.com` | jkcoxson | macOS (was Windows) | second instance, different machine IDs |
| 3 | `ani.sidestore.io` | SideStore team | macOS | long-running, official |

Every other entry in that curated list is treated the same way and is tried next, in SideStore's own
order. Two deliberate rules:

- **HTTPS only.** The public list contains at least one plain `http://` server. Anisette headers do not
  belong in clear text, so those entries are dropped.
- **No credentials ever go there**, so "which server" is a privacy question (who sees your IP), not a
  password-leak question.

## Pinning one, or hosting your own

```
sh bin/barisign login --server https://ani.jkcoxson.com    # pin
sh bin/barisign login --probe                              # test without a password
```

To run your own, the SideStore docs walk through deploying `omnisette-server` (Docker image
`ghcr.io/sidestore/omnisette-server`, or a Render free instance) — it needs the platform's ADI libraries.
Then pin it with `--server`. Nothing in BariSign requires a specific server.
