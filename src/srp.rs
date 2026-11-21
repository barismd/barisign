//! SRP-6a (RFC 5054 groups), both roles:
//! * server, for Apple pair-setup (3072-bit group, SHA-512, user "Pair-Setup");
//! * client, for Apple's GrandSlam sign-in (2048-bit group, SHA-256).
//!
//! Conventions (as Apple's CoreUtils and the RustCrypto `srp` crate use them):
//! k = H(N | PAD(g)), u = H(PAD(A) | PAD(B)), K = H(S),
//! M1 = H(H(N) xor H(g') | H(I) | s | A | B | K), M2 = H(A | M1 | K),
//! numbers hashed as minimal big-endian bytes unless padded (PAD = |N| bytes).
//! g' is g for pair-setup (device-proven) and PAD(g) for GrandSlam (srp
//! 0.7.0-rc.3 `compute_m1_rfc5054(g_no_pad = false)`, which isideload signs in
//! with; H(g) there makes Apple answer -22406 "enter the correct password").

use num_bigint::BigUint;
use sha2::{Digest, Sha256, Sha512};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hash {
    Sha256,
    Sha512,
}

impl Hash {
    pub fn digest(self, parts: &[&[u8]]) -> Vec<u8> {
        match self {
            Hash::Sha256 => {
                let mut h = Sha256::new();
                for p in parts {
                    h.update(p);
                }
                h.finalize().to_vec()
            }
            Hash::Sha512 => {
                let mut h = Sha512::new();
                for p in parts {
                    h.update(p);
                }
                h.finalize().to_vec()
            }
        }
    }
}

pub struct Group {
    pub n: BigUint,
    pub g: BigUint,
    pub hash: Hash,
    /// M1 hashes PAD(g) instead of g.
    pad_g_in_m1: bool,
}

const N_2048: &str = "AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050\
A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50\
E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B8\
55F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773B\
CA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748\
544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6\
AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB6\
94B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73";

const N_3072: &str = "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74\
020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437\
4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED\
EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05\
98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB\
9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B\
E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718\
3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33\
A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7\
ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864\
D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E2\
08E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF";

impl Group {
    pub fn rfc5054_2048_sha256() -> Self {
        Group { n: BigUint::parse_bytes(N_2048.as_bytes(), 16).unwrap(), g: 2u32.into(), hash: Hash::Sha256,
                pad_g_in_m1: true }
    }

    pub fn rfc5054_3072_sha512() -> Self {
        Group { n: BigUint::parse_bytes(N_3072.as_bytes(), 16).unwrap(), g: 5u32.into(), hash: Hash::Sha512,
                pad_g_in_m1: false }
    }

    pub fn len(&self) -> usize {
        ((self.n.bits() + 7) / 8) as usize
    }

    fn pad(&self, v: &BigUint) -> Vec<u8> {
        let raw = v.to_bytes_be();
        let mut out = vec![0u8; self.len().saturating_sub(raw.len())];
        out.extend_from_slice(&raw);
        out
    }

    fn hn(&self, parts: &[&[u8]]) -> BigUint {
        BigUint::from_bytes_be(&self.hash.digest(parts))
    }

    fn k(&self) -> BigUint {
        self.hn(&[&self.n.to_bytes_be(), &self.pad(&self.g)])
    }

    fn u(&self, a: &BigUint, b: &BigUint) -> BigUint {
        self.hn(&[&self.pad(a), &self.pad(b)])
    }

    /// `b` as the peer sent it (srp 0.7 hashes B's bytes as received).
    fn m1(&self, user: &[u8], salt: &[u8], a: &BigUint, b: &[u8], key: &[u8]) -> Vec<u8> {
        let hn = self.hash.digest(&[&self.n.to_bytes_be()]);
        let g = if self.pad_g_in_m1 { self.pad(&self.g) } else { self.g.to_bytes_be() };
        let hg = self.hash.digest(&[&g]);
        let xor: Vec<u8> = hn.iter().zip(hg.iter()).map(|(x, y)| x ^ y).collect();
        let hi = self.hash.digest(&[user]);
        self.hash.digest(&[&xor, &hi, salt, &a.to_bytes_be(), b, key])
    }

    fn m2(&self, a: &BigUint, m1: &[u8], key: &[u8]) -> Vec<u8> {
        self.hash.digest(&[&a.to_bytes_be(), m1, key])
    }
}

/// Constant-time byte comparison.
pub fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// x = H(s | H(I ":" P)); the pair-setup convention.
pub fn x_with_user(group: &Group, salt: &[u8], user: &[u8], password: &[u8]) -> BigUint {
    let inner = group.hash.digest(&[user, b":", password]);
    group.hn(&[salt, &inner])
}

/// x = H(s | H(":" P)); GrandSlam leaves the user out of x.
pub fn x_without_user(group: &Group, salt: &[u8], password: &[u8]) -> BigUint {
    let inner = group.hash.digest(&[b":", password]);
    group.hn(&[salt, &inner])
}

pub struct Server<'g> {
    group: &'g Group,
    user: Vec<u8>,
    salt: Vec<u8>,
    v: BigUint,
    b: BigUint,
    big_b: BigUint,
}

pub struct ServerSession {
    pub key: Vec<u8>,
    pub client_proof: Vec<u8>,
    pub server_proof: Vec<u8>,
}

impl<'g> Server<'g> {
    pub fn new(group: &'g Group, user: &[u8], salt: &[u8], x: &BigUint) -> Self {
        let v = group.g.modpow(x, &group.n);
        loop {
            let b = BigUint::from_bytes_be(&rand::random::<[u8; 32]>());
            let big_b = (group.k() * &v + group.g.modpow(&b, &group.n)) % &group.n;
            // Apple's side expects B to fill the whole group width.
            if big_b.to_bytes_be().len() == group.len() {
                return Server { group, user: user.to_vec(), salt: salt.to_vec(), v, b, big_b };
            }
        }
    }

    pub fn public(&self) -> Vec<u8> {
        self.big_b.to_bytes_be()
    }

    pub fn process(&self, client_public: &[u8]) -> Result<ServerSession, String> {
        let g = self.group;
        let a = BigUint::from_bytes_be(client_public);
        if (&a % &g.n) == BigUint::from(0u32) {
            return Err("invalid SRP client public value".into());
        }
        let u = g.u(&a, &self.big_b);
        let s = (&a * self.v.modpow(&u, &g.n)).modpow(&self.b, &g.n);
        let key = g.hash.digest(&[&s.to_bytes_be()]);
        let client_proof = g.m1(&self.user, &self.salt, &a, &self.big_b.to_bytes_be(), &key);
        let server_proof = g.m2(&a, &client_proof, &key);
        Ok(ServerSession { key, client_proof, server_proof })
    }
}

pub struct Client<'g> {
    group: &'g Group,
    a: BigUint,
    big_a: BigUint,
}

pub struct ClientSession {
    /// The premaster secret S (minimal bytes): GrandSlam derives the key of
    /// its encrypted session data from S itself (isideload `usr.key()`).
    pub premaster: Vec<u8>,
    pub proof: Vec<u8>,
    expected_server_proof: Vec<u8>,
}

impl ClientSession {
    pub fn verify_server(&self, proof: &[u8]) -> bool {
        same(&self.expected_server_proof, proof)
    }
}

impl<'g> Client<'g> {
    pub fn new(group: &'g Group) -> Self {
        // A full-width A makes padded and unpadded hashing agree.
        loop {
            let a = BigUint::from_bytes_be(&rand::random::<[u8; 32]>());
            let big_a = group.g.modpow(&a, &group.n);
            if big_a.to_bytes_be().len() == group.len() {
                return Client { group, a, big_a };
            }
        }
    }

    #[cfg(test)]
    fn with_secret(group: &'g Group, a: BigUint) -> Self {
        let big_a = group.g.modpow(&a, &group.n);
        Client { group, a, big_a }
    }

    pub fn public(&self) -> Vec<u8> {
        self.big_a.to_bytes_be()
    }

    /// `x` from `x_with_user` / `x_without_user`.
    pub fn process(&self, user: &[u8], salt: &[u8], server_public: &[u8], x: &BigUint)
        -> Result<ClientSession, String>
    {
        let g = self.group;
        let big_b = BigUint::from_bytes_be(server_public);
        if (&big_b % &g.n) == BigUint::from(0u32) {
            return Err("invalid SRP server public value".into());
        }
        let u = g.u(&self.big_a, &big_b);
        let kv = (g.k() * g.g.modpow(x, &g.n)) % &g.n;
        let base = ((&big_b + &g.n) - kv) % &g.n;
        let s = base.modpow(&(&self.a + &u * x), &g.n);
        let premaster = s.to_bytes_be();
        let key = g.hash.digest(&[&premaster]);
        let proof = g.m1(user, salt, &self.big_a, server_public, &key);
        let expected_server_proof = g.m2(&self.big_a, &proof, &key);
        Ok(ClientSession { premaster, proof, expected_server_proof })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_and_server_agree_pair_setup() {
        let group = Group::rfc5054_3072_sha512();
        let salt = [9u8; 16];
        let x = x_with_user(&group, &salt, b"Pair-Setup", b"123456");
        let server = Server::new(&group, b"Pair-Setup", &salt, &x);
        assert_eq!(server.public().len(), 384);
        let client = Client::new(&group);
        let c = client.process(b"Pair-Setup", &salt, &server.public(), &x).unwrap();
        let s = server.process(&client.public()).unwrap();
        assert!(same(&c.proof, &s.client_proof));
        assert!(c.verify_server(&s.server_proof));
        assert_eq!(Sha512::digest(&c.premaster).to_vec(), s.key);
    }

    #[test]
    fn wrong_code_is_detected() {
        let group = Group::rfc5054_3072_sha512();
        let salt = [1u8; 16];
        let good = x_with_user(&group, &salt, b"Pair-Setup", b"111111");
        let bad = x_with_user(&group, &salt, b"Pair-Setup", b"222222");
        let server = Server::new(&group, b"Pair-Setup", &salt, &good);
        let client = Client::new(&group);
        let c = client.process(b"Pair-Setup", &salt, &server.public(), &bad).unwrap();
        let s = server.process(&client.public()).unwrap();
        assert!(!same(&c.proof, &s.client_proof));
    }

    #[test]
    fn grandslam_shape_agrees() {
        let group = Group::rfc5054_2048_sha256();
        let salt = [3u8; 16];
        let x = x_without_user(&group, &salt, b"derived-password-key");
        let server = Server::new(&group, b"user@example.com", &salt, &x);
        let client = Client::new(&group);
        let c = client.process(b"user@example.com", &salt, &server.public(), &x).unwrap();
        let s = server.process(&client.public()).unwrap();
        assert!(same(&c.proof, &s.client_proof));
        assert!(c.verify_server(&s.server_proof));
    }
}

#[cfg(test)]
mod interop {
    //! Our server against idevice-srp's client (the one idevice uses with iPhones).
    use super::*;
    use idevice_srp::client::SrpClient;
    use idevice_srp::groups::G_3072;

    #[test]
    fn idevice_srp_client_accepts_our_server() {
        let group = Group::rfc5054_3072_sha512();
        let salt = [5u8; 16];
        let x = x_with_user(&group, &salt, b"Pair-Setup", b"654321");
        let server = Server::new(&group, b"Pair-Setup", &salt, &x);
        let client = SrpClient::<sha2::Sha512>::new(&G_3072);
        let a = [7u8; 32];
        let a_pub = client.compute_public_ephemeral(&a);
        let verifier = client
            .process_reply(&a, b"Pair-Setup", b"654321", &salt, &server.public(), false)
            .unwrap();
        let session = server.process(&a_pub).unwrap();
        assert_eq!(verifier.proof(), session.client_proof.as_slice());
        assert!(verifier.verify_server(&session.server_proof).is_ok());
        assert_eq!(verifier.key(), session.key.as_slice());
    }

    #[test]
    fn grandslam_client_matches_the_python_sign_in() {
        // Vector from the earlier Python sign-in (device-proven, a port of
        // what isideload gets from srp 0.7.0-rc.3): a = 07…07, b = 05…05,
        // salt 00..0f, password key 42…42.
        let group = Group::rfc5054_2048_sha256();
        let client = Client::with_secret(&group, BigUint::from_bytes_be(&[7u8; 32]));
        let b_pub = hex::decode(concat!(
            "5ed0b35d53a999d45bc4972fbcc70eed825fe30c1e47242189c5f40e2f17dbc7",
            "45024eb2e6bd76dddf10fd7a6e20395bb9a4e596700adb971b33fb1ae9beb849",
            "0d7124c481340eedd6737e1e74b1ab2b119c556dfc43bd482f9734b9c2a7621f",
            "92144a175ca321a54fd09b1b93bb89b64f255473f0209d83d6e1522138db5651",
            "13c1db3118d93f969e094e9012ee39352d354e30acd3d8e109f9b03d8424cd10",
            "4634d62e0f64c836581996adb663cbf61810e2e52879cc890657275663abc86c",
            "3559d348b95e65b06b208b0d836ca28e96c9ea35fde48b5ffdf42d9465db7f4f",
            "333762e12e44ff6c23964572dd7553a08a952723d2599c3a644e8a69e323e369",)).unwrap();
        let salt: Vec<u8> = (0u8..16).collect();
        let x = x_without_user(&group, &salt, &[0x42u8; 32]);
        let session = client.process(b"someone@icloud.com", &salt, &b_pub, &x).unwrap();
        assert_eq!(hex::encode(&session.proof), "e3093018ead5d2d94060f8234b5c54f40b0749f95dc0419099096f2e8b468adc");
        assert_eq!(hex::encode(&session.premaster[..32]), "3f705becd05cbdbbc79b819dbd36ace57cab341e77bcc0fcc4699ea5a722b285");
    }
}
