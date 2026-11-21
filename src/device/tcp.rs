//! A small userspace TCP over IPv6, enough for outbound connections through the
//! CDTunnel (a byte stream of raw IPv6 packets inside TLS). One actor task owns
//! every connection; the link reader and writer are separate tasks, so the
//! actor never blocks on the network.
//!
//! Scope: active open, in-order delivery with an out-of-order queue, our window
//! fixed at 64 KiB (window scale option with shift 0), the peer's window scaled
//! as its SYN-ACK says (RFC 7323), at most 1 MiB in flight, MSS from the
//! tunnel MTU (at most 8192 - 60, as idevice's jktcp sets it) and the SYN-ACK,
//! timer-based retransmission of the oldest unacknowledged segment with
//! exponential back-off.

use std::collections::{BTreeMap, HashMap};
use std::net::Ipv6Addr;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, oneshot};

const FIN: u8 = 0x01;
const SYN: u8 = 0x02;
const RST: u8 = 0x04;
const PSH: u8 = 0x08;
const ACK: u8 = 0x10;
const OUR_WINDOW: u16 = 65535;
const SEND_BUFFER: usize = 1 << 20;
/// Bytes in flight at most, whatever the peer's window says.
const SEND_WINDOW: usize = 1 << 20;
/// Before the SYN-ACK names one: the IPv6 minimum MTU minus headers.
const DEFAULT_MSS: usize = 1220;
const TICK: Duration = Duration::from_millis(50);

fn lt(a: u32, b: u32) -> bool {
    (a.wrapping_sub(b) as i32) < 0
}
fn le(a: u32, b: u32) -> bool {
    a == b || lt(a, b)
}

/// RFC 1071 checksum over `parts`; every part but the last must have an
/// even length (the pseudo-header fields do), so 16-bit words never straddle.
fn checksum(parts: &[&[u8]]) -> u16 {
    let mut sum: u64 = 0;
    for part in parts {
        let mut words = part.chunks_exact(2);
        for w in &mut words {
            sum += u64::from(u16::from_be_bytes([w[0], w[1]]));
        }
        if let [hi] = words.remainder() {
            sum += u64::from(u16::from_be_bytes([*hi, 0]));
        }
    }
    while sum > 0xFFFF {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }
    !(sum as u16)
}

/// Bytes written but not yet acknowledged. Acknowledged bytes are dropped
/// by moving a start offset; the buffer is compacted only when that offset
/// passes half of it, so an ACK never moves the whole buffer.
#[derive(Default)]
struct SendBuf {
    buf: Vec<u8>,
    start: usize,
}

impl SendBuf {
    fn len(&self) -> usize {
        self.buf.len() - self.start
    }

    fn is_empty(&self) -> bool {
        self.len() == 0
    }

    fn range(&self, from: usize, to: usize) -> &[u8] {
        &self.buf[self.start + from..self.start + to]
    }

    fn push(&mut self, data: &[u8]) {
        self.buf.extend_from_slice(data);
    }

    fn consume(&mut self, n: usize) {
        self.start += n.min(self.len());
        if self.start == self.buf.len() {
            self.buf.clear();
            self.start = 0;
        } else if self.start > 256 * 1024 && self.start * 2 > self.buf.len() {
            self.buf.drain(..self.start);
            self.start = 0;
        }
    }
}

#[derive(Debug)]
enum Incoming {
    Data(Vec<u8>),
    Eof,
    Error(String),
}

enum Cmd {
    Connect { port: u16, reply: oneshot::Sender<Result<TcpConn, String>> },
    Write { id: u16, data: Vec<u8>, done: oneshot::Sender<Result<(), String>> },
    Close { id: u16 },
    Packet(Vec<u8>),
    LinkDown(String),
}

#[derive(PartialEq, Eq, Debug)]
enum State {
    SynSent,
    Established,
}

struct Conn {
    remote_port: u16,
    state: State,
    iss: u32,
    snd_una: u32,
    snd_nxt: u32,
    rcv_nxt: u32,
    peer_window: u32,
    /// The peer's window scale (from its SYN-ACK; 0 when it sent none).
    peer_shift: u8,
    mss: usize,
    out: SendBuf,          // bytes from snd_una on (in flight + unsent)
    ooo: BTreeMap<u32, Vec<u8>>,
    rto: Duration,
    retransmit_at: Instant,
    syn_deadline: Instant,
    fin_sent: bool,
    fin_received: bool,
    closing: Option<Instant>,
    connect_reply: Option<oneshot::Sender<Result<TcpConn, String>>>,
    to_app: mpsc::UnboundedSender<Incoming>,
    from_app: Option<mpsc::UnboundedReceiver<Incoming>>,
    writers: Vec<oneshot::Sender<Result<(), String>>>,
}

impl Conn {
    fn in_flight(&self) -> usize {
        (self.snd_nxt.wrapping_sub(self.snd_una) as usize).min(self.out.len())
    }
}

struct Actor {
    local: Ipv6Addr,
    remote: Ipv6Addr,
    /// The largest segment the tunnel carries.
    max_mss: usize,
    /// Scratch list of connection ids for `tick`.
    tick_ids: Vec<u16>,
    conns: HashMap<u16, Conn>,
    link_tx: mpsc::UnboundedSender<Vec<u8>>,
    cmd_tx: mpsc::UnboundedSender<Cmd>,
    down: Option<String>,
}

impl Actor {
    fn segment(&self, local_port: u16, c: &Conn, flags: u8, seq: u32, payload: &[u8], options: &[u8]) {
        let header_len = 20 + options.len();
        let mut tcp = Vec::with_capacity(header_len + payload.len());
        tcp.extend_from_slice(&local_port.to_be_bytes());
        tcp.extend_from_slice(&c.remote_port.to_be_bytes());
        tcp.extend_from_slice(&seq.to_be_bytes());
        let ack = if flags & ACK != 0 { c.rcv_nxt } else { 0 };
        tcp.extend_from_slice(&ack.to_be_bytes());
        tcp.push(((header_len / 4) as u8) << 4);
        tcp.push(flags);
        tcp.extend_from_slice(&OUR_WINDOW.to_be_bytes());
        tcp.extend_from_slice(&[0, 0, 0, 0]);
        tcp.extend_from_slice(options);
        tcp.extend_from_slice(payload);
        let len = (tcp.len() as u32).to_be_bytes();
        let sum = checksum(&[&self.local.octets(), &self.remote.octets(), &len, &[0, 0, 0, 6], &tcp]);
        tcp[16..18].copy_from_slice(&sum.to_be_bytes());
        let mut packet = Vec::with_capacity(40 + tcp.len());
        packet.extend_from_slice(&[0x60, 0, 0, 0]);
        packet.extend_from_slice(&(tcp.len() as u16).to_be_bytes());
        packet.push(6);
        packet.push(64);
        packet.extend_from_slice(&self.local.octets());
        packet.extend_from_slice(&self.remote.octets());
        packet.extend_from_slice(&tcp);
        let _ = self.link_tx.send(packet);
    }

    fn send_ack(&self, id: u16) {
        if let Some(c) = self.conns.get(&id) {
            if c.state == State::Established {
                self.segment(id, c, ACK, c.snd_nxt, &[], &[]);
            }
        }
    }

    fn pump(&mut self, id: u16) {
        let Some(c) = self.conns.get(&id) else { return };
        if c.state != State::Established {
            return;
        }
        let mut sent = c.in_flight();
        let mut seq = c.snd_nxt;
        let mut segments = Vec::new();
        let window = (c.peer_window as usize).min(SEND_WINDOW);
        while sent < c.out.len() {
            let room = window.saturating_sub(sent);
            if room == 0 {
                break;
            }
            let n = (c.out.len() - sent).min(c.mss).min(room);
            segments.push((seq, c.out.range(sent, sent + n).to_vec()));
            seq = seq.wrapping_add(n as u32);
            sent += n;
        }
        if segments.is_empty() {
            return;
        }
        for (s, data) in &segments {
            self.segment(id, c, ACK | PSH, *s, data, &[]);
        }
        let c = self.conns.get_mut(&id).unwrap();
        c.snd_nxt = seq;
        c.retransmit_at = Instant::now() + c.rto;
    }

    fn release_writers(c: &mut Conn) {
        if c.out.len() < SEND_BUFFER {
            for w in c.writers.drain(..) {
                let _ = w.send(Ok(()));
            }
        }
    }

    fn fail(&mut self, id: u16, why: &str) {
        if let Some(mut c) = self.conns.remove(&id) {
            if let Some(reply) = c.connect_reply.take() {
                let _ = reply.send(Err(why.to_string()));
            }
            let _ = c.to_app.send(Incoming::Error(why.to_string()));
            for w in c.writers.drain(..) {
                let _ = w.send(Err(why.to_string()));
            }
        }
    }

    fn connect(&mut self, port: u16, reply: oneshot::Sender<Result<TcpConn, String>>) {
        if let Some(why) = &self.down {
            let _ = reply.send(Err(why.clone()));
            return;
        }
        let mut id;
        loop {
            id = 40000 + rand::random::<u16>() % 20000;
            if !self.conns.contains_key(&id) {
                break;
            }
        }
        let iss: u32 = rand::random();
        let (to_app, from_app) = mpsc::unbounded_channel();
        let now = Instant::now();
        let conn = Conn {
            remote_port: port, state: State::SynSent, iss, snd_una: iss, snd_nxt: iss.wrapping_add(1),
            rcv_nxt: 0, peer_window: 65535, peer_shift: 0, mss: DEFAULT_MSS, out: SendBuf::default(), ooo: BTreeMap::new(),
            rto: Duration::from_millis(400), retransmit_at: now + Duration::from_secs(1),
            syn_deadline: now + Duration::from_secs(10), fin_sent: false, fin_received: false, closing: None,
            connect_reply: Some(reply), to_app, from_app: Some(from_app), writers: Vec::new(),
        };
        self.segment(id, &conn, SYN, iss, &[], &self.syn_options());
        self.conns.insert(id, conn);
    }

    /// MSS (our receive size: the tunnel's) and window scale 0, which lets
    /// the peer scale its own window.
    fn syn_options(&self) -> [u8; 8] {
        let [hi, lo] = (self.max_mss as u16).to_be_bytes();
        [2, 4, hi, lo, 1, 3, 3, 0]
    }

    fn packet(&mut self, p: &[u8]) {
        if p.len() < 40 || p[0] >> 4 != 6 || p[24..40] != self.local.octets() {
            return;
        }
        let mut next = p[6];
        let mut at = 40usize;
        while matches!(next, 0 | 43 | 44 | 60) {
            if p.len() < at + 8 {
                return;
            }
            next = p[at];
            at += if next == 44 { 8 } else { (p[at + 1] as usize + 1) * 8 };
        }
        if next != 6 || p.len() < at + 20 {
            return;
        }
        let t = &p[at..];
        let src = u16::from_be_bytes([t[0], t[1]]);
        let dst = u16::from_be_bytes([t[2], t[3]]);
        let seq = u32::from_be_bytes([t[4], t[5], t[6], t[7]]);
        let ack = u32::from_be_bytes([t[8], t[9], t[10], t[11]]);
        let off = (t[12] >> 4) as usize * 4;
        let flags = t[13];
        let window = u16::from_be_bytes([t[14], t[15]]) as u32;
        if off < 20 || t.len() < off {
            return;
        }
        let payload = &t[off..];
        let Some(c) = self.conns.get_mut(&dst) else { return };
        if c.remote_port != src {
            return;
        }
        if flags & RST != 0 {
            self.fail(dst, "the iPhone reset the connection");
            return;
        }
        if c.state == State::SynSent {
            if flags & SYN != 0 && flags & ACK != 0 && ack == c.iss.wrapping_add(1) {
                if let Some(mss) = tcp_option(&t[20..off], 2).filter(|v| v.len() == 2) {
                    c.mss = (u16::from_be_bytes([mss[0], mss[1]]) as usize).clamp(536, self.max_mss);
                }
                c.peer_shift = tcp_option(&t[20..off], 3).and_then(|v| v.first().copied()).unwrap_or(0).min(14);
                c.snd_una = ack;
                c.rcv_nxt = seq.wrapping_add(1);
                c.peer_window = window;
                c.state = State::Established;
                let handle = TcpConn {
                    id: dst, cmd: self.cmd_tx.clone(), rx: c.from_app.take().unwrap(),
                    buf: Vec::new(), eof: false,
                };
                if let Some(reply) = c.connect_reply.take() {
                    let _ = reply.send(Ok(handle));
                }
                self.send_ack(dst);
            }
            return;
        }
        if c.state != State::Established {
            return;
        }
        if flags & ACK != 0 && le(c.snd_una, ack) && le(ack, c.snd_nxt) {
            let acked = ack.wrapping_sub(c.snd_una) as usize;
            c.out.consume(acked);
            c.snd_una = ack;
            if acked > 0 {
                c.rto = Duration::from_millis(400);
                c.retransmit_at = Instant::now() + c.rto;
            }
            c.peer_window = window << c.peer_shift;
            Self::release_writers(c);
            if c.closing.is_some() && c.out.is_empty() {
                self.close(dst);
                return;
            }
        }
        let mut need_ack = false;
        if !payload.is_empty() {
            need_ack = true;
            if seq == c.rcv_nxt {
                c.rcv_nxt = c.rcv_nxt.wrapping_add(payload.len() as u32);
                let _ = c.to_app.send(Incoming::Data(payload.to_vec()));
                while let Some((&s, _)) = c.ooo.iter().next() {
                    if s == c.rcv_nxt {
                        let data = c.ooo.remove(&s).unwrap();
                        c.rcv_nxt = c.rcv_nxt.wrapping_add(data.len() as u32);
                        let _ = c.to_app.send(Incoming::Data(data));
                    } else if lt(s, c.rcv_nxt) {
                        c.ooo.remove(&s);
                    } else {
                        break;
                    }
                }
            } else if lt(c.rcv_nxt, seq) {
                c.ooo.insert(seq, payload.to_vec());
            }
        }
        if flags & FIN != 0 && seq.wrapping_add(payload.len() as u32) == c.rcv_nxt && !c.fin_received {
            c.rcv_nxt = c.rcv_nxt.wrapping_add(1);
            c.fin_received = true;
            let _ = c.to_app.send(Incoming::Eof);
            need_ack = true;
        }
        if need_ack {
            self.send_ack(dst);
        }
        self.pump(dst);
    }

    fn tick(&mut self) {
        if self.conns.is_empty() {
            return;
        }
        let now = Instant::now();
        // Reused: the tick runs every 50 ms for as long as a tunnel is open.
        let mut ids = std::mem::take(&mut self.tick_ids);
        ids.clear();
        ids.extend(self.conns.keys().copied());
        for &id in &ids {
            if self.conns[&id].closing.is_some() {
                self.close(id);
                if !self.conns.contains_key(&id) {
                    continue;
                }
            }
            let c = &self.conns[&id];
            if c.state == State::SynSent {
                if now > c.syn_deadline {
                    self.fail(id, "the iPhone did not answer the TCP connection (service port closed?)");
                } else if now >= c.retransmit_at {
                    self.segment(id, c, SYN, c.iss, &[], &self.syn_options());
                    let c = self.conns.get_mut(&id).unwrap();
                    c.retransmit_at = now + Duration::from_secs(1);
                }
                continue;
            }
            let in_flight = c.in_flight();
            if in_flight > 0 && now >= c.retransmit_at {
                let n = in_flight.min(c.mss);
                self.segment(id, c, ACK | PSH, c.snd_una, c.out.range(0, n), &[]);
                let c = self.conns.get_mut(&id).unwrap();
                c.rto = (c.rto * 2).min(Duration::from_secs(4));
                c.retransmit_at = now + c.rto;
            } else if in_flight < c.out.len() {
                self.pump(id);
            }
        }
        self.tick_ids = ids;
    }

    /// FIN once everything written has been acknowledged (or after 15 s).
    fn close(&mut self, id: u16) {
        let Some(c) = self.conns.get_mut(&id) else { return };
        if c.state != State::Established {
            self.conns.remove(&id);
            return;
        }
        let since = *c.closing.get_or_insert_with(Instant::now);
        if c.out.is_empty() || since.elapsed() > Duration::from_secs(15) {
            if !c.fin_sent {
                c.fin_sent = true;
                let c = &self.conns[&id];
                self.segment(id, c, FIN | ACK, c.snd_nxt, &[], &[]);
            }
            self.conns.remove(&id);
        }
    }
}

fn tcp_option(options: &[u8], kind: u8) -> Option<&[u8]> {
    let mut at = 0;
    while at < options.len() {
        match options[at] {
            0 => return None,
            1 => at += 1,
            k => {
                let len = *options.get(at + 1)? as usize;
                if len < 2 || at + len > options.len() {
                    return None;
                }
                if k == kind {
                    return Some(&options[at + 2..at + len]);
                }
                at += len;
            }
        }
    }
    None
}

/// One TCP connection through the tunnel.
pub struct TcpConn {
    id: u16,
    cmd: mpsc::UnboundedSender<Cmd>,
    rx: mpsc::UnboundedReceiver<Incoming>,
    buf: Vec<u8>,
    eof: bool,
}

impl TcpConn {
    pub async fn write_all(&mut self, data: &[u8]) -> std::io::Result<()> {
        let (done, wait) = oneshot::channel();
        self.cmd.send(Cmd::Write { id: self.id, data: data.to_vec(), done })
            .map_err(|_| broken("the tunnel is closed"))?;
        wait.await.map_err(|_| broken("the tunnel is closed"))?.map_err(broken)
    }

    async fn fill(&mut self) -> std::io::Result<()> {
        if self.eof {
            return Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "the iPhone closed the connection"));
        }
        match self.rx.recv().await {
            Some(Incoming::Data(d)) => {
                self.buf.extend_from_slice(&d);
                Ok(())
            }
            Some(Incoming::Eof) | None => {
                self.eof = true;
                Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "the iPhone closed the connection"))
            }
            Some(Incoming::Error(e)) => Err(broken(e)),
        }
    }

    pub async fn read_exact(&mut self, n: usize) -> std::io::Result<Vec<u8>> {
        while self.buf.len() < n {
            self.fill().await?;
        }
        Ok(self.buf.drain(..n).collect())
    }

    /// Whatever has arrived (at least one byte).
    #[cfg(test)]
    pub async fn read_some(&mut self) -> std::io::Result<Vec<u8>> {
        if self.buf.is_empty() {
            self.fill().await?;
        }
        Ok(std::mem::take(&mut self.buf))
    }
}

impl Drop for TcpConn {
    fn drop(&mut self) {
        let _ = self.cmd.send(Cmd::Close { id: self.id });
    }
}

fn broken(why: impl Into<String>) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::BrokenPipe, why.into())
}

/// The stack bound to one tunnel.
#[derive(Clone)]
pub struct Stack {
    cmd: mpsc::UnboundedSender<Cmd>,
}

impl Stack {
    /// `incoming` delivers raw IPv6 packets from the link; packets to send go
    /// to the returned receiver (the link writer drains it). `mtu` is the
    /// tunnel's (CDTunnel handshake).
    pub fn start(local: Ipv6Addr, remote: Ipv6Addr, mtu: usize) -> (Stack, mpsc::UnboundedReceiver<Vec<u8>>) {
        let (cmd_tx, mut cmd_rx) = mpsc::unbounded_channel::<Cmd>();
        let (link_tx, link_rx) = mpsc::unbounded_channel();
        let max_mss = mtu.clamp(1280, 8192) - 60;
        let mut actor = Actor { local, remote, max_mss, tick_ids: Vec::new(), conns: HashMap::new(), link_tx, cmd_tx: cmd_tx.clone(), down: None };
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(TICK);
            loop {
                tokio::select! {
                    cmd = cmd_rx.recv() => match cmd {
                        Some(Cmd::Packet(p)) => actor.packet(&p),
                        Some(Cmd::Connect { port, reply }) => actor.connect(port, reply),
                        Some(Cmd::Write { id, data, done }) => {
                            match actor.conns.get_mut(&id) {
                                Some(c) if c.state == State::Established => {
                                    c.out.push(&data);
                                    if c.out.len() < SEND_BUFFER {
                                        let _ = done.send(Ok(()));
                                    } else {
                                        c.writers.push(done);
                                    }
                                    actor.pump(id);
                                }
                                _ => { let _ = done.send(Err("connection closed".into())); }
                            }
                        }
                        Some(Cmd::Close { id }) => actor.close(id),
                        Some(Cmd::LinkDown(why)) => {
                            let ids: Vec<u16> = actor.conns.keys().copied().collect();
                            for id in ids {
                                actor.fail(id, &why);
                            }
                            actor.down = Some(why);
                        }
                        None => break,
                    },
                    _ = ticker.tick() => actor.tick(),
                }
            }
        });
        (Stack { cmd: cmd_tx }, link_rx)
    }

    pub fn deliver(&self, packet: Vec<u8>) {
        let _ = self.cmd.send(Cmd::Packet(packet));
    }

    pub fn link_down(&self, why: String) {
        let _ = self.cmd.send(Cmd::LinkDown(why));
    }

    pub async fn connect(&self, port: u16) -> Result<TcpConn, String> {
        let (reply, wait) = oneshot::channel();
        self.cmd.send(Cmd::Connect { port, reply }).map_err(|_| "the tunnel is closed".to_string())?;
        tokio::time::timeout(Duration::from_secs(15), wait)
            .await
            .map_err(|_| format!("TCP connection to port {port} timed out"))?
            .map_err(|_| "the tunnel is closed".to_string())?
    }
}

/// Splits a byte stream into IPv6 packets.
#[derive(Default)]
pub struct Reassembler {
    buf: Vec<u8>,
}

impl Reassembler {
    pub fn push(&mut self, data: &[u8]) -> Vec<Vec<u8>> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        while self.buf.len() >= 40 {
            let total = 40 + u16::from_be_bytes([self.buf[4], self.buf[5]]) as usize;
            if self.buf.len() < total {
                break;
            }
            out.push(self.buf.drain(..total).collect());
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_over_parts_with_an_odd_tail() {
        let a = [0x12u8, 0x34, 0x56, 0x78];
        let b = [0x9au8, 0xbc, 0xde];
        let whole: Vec<u8> = a.iter().chain(b.iter()).copied().collect();
        assert_eq!(checksum(&[&a, &b]), checksum(&[&whole]));
        // 1234 + 5678 + 9abc + de00 = 1_e168 -> e169 -> !e169 = 1e96
        assert_eq!(checksum(&[&whole]), 0x1e96);
    }

    #[test]
    fn checksum_matches_rfc1071_example() {
        // RFC 1071 §3 example words: 0001 f203 f4f5 f6f7 -> sum ddf2 -> ~ = 220d
        assert_eq!(checksum(&[&[0x00, 0x01, 0xf2, 0x03, 0xf4, 0xf5, 0xf6, 0xf7]]), 0x220d);
    }

    #[test]
    fn reassembler_splits_packets() {
        let mut p1 = vec![0x60, 0, 0, 0, 0, 2, 6, 64];
        p1.extend([0u8; 32]);
        p1.extend([1, 2]);
        let mut stream = p1.clone();
        stream.extend_from_slice(&p1[..10]);
        let mut r = Reassembler::default();
        assert_eq!(r.push(&stream).len(), 1);
        assert_eq!(r.push(&p1[10..]).len(), 1);
    }
}

#[cfg(test)]
mod interop {
    //! Our stack against smoltcp, an independent TCP/IPv6 implementation that
    //! plays the iPhone: it listens, echoes everything back, and we check a
    //! large transfer arrives intact in both directions.
    use super::*;
    use smoltcp::iface::{Config, Interface, SocketSet};
    use smoltcp::phy::{Device, DeviceCapabilities, Medium, RxToken, TxToken};
    use smoltcp::socket::tcp;
    use smoltcp::time::Instant as SInstant;
    use smoltcp::wire::{HardwareAddress, IpAddress, IpCidr};
    use std::collections::VecDeque;

    struct Dev {
        rx: VecDeque<Vec<u8>>,
        tx: Vec<Vec<u8>>,
        mtu: usize,
    }
    struct Rx(Vec<u8>);
    struct Tx<'a>(&'a mut Vec<Vec<u8>>);
    impl RxToken for Rx {
        fn consume<R, F: FnOnce(&[u8]) -> R>(self, f: F) -> R { f(&self.0) }
    }
    impl TxToken for Tx<'_> {
        fn consume<R, F: FnOnce(&mut [u8]) -> R>(self, len: usize, f: F) -> R {
            let mut buf = vec![0u8; len];
            let r = f(&mut buf);
            self.0.push(buf);
            r
        }
    }
    impl Device for Dev {
        type RxToken<'a> = Rx;
        type TxToken<'a> = Tx<'a>;
        fn receive(&mut self, _: SInstant) -> Option<(Rx, Tx<'_>)> {
            let p = self.rx.pop_front()?;
            Some((Rx(p), Tx(&mut self.tx)))
        }
        fn transmit(&mut self, _: SInstant) -> Option<Tx<'_>> { Some(Tx(&mut self.tx)) }
        fn capabilities(&self) -> DeviceCapabilities {
            let mut c = DeviceCapabilities::default();
            c.medium = Medium::Ip;
            c.max_transmission_unit = self.mtu;
            c
        }
    }

    #[tokio::test]
    async fn echo_through_smoltcp() {
        echo(1500, 65536, 300_000).await;
    }

    /// Jumbo segments (the tunnel's MTU) and a peer window above 64 KiB,
    /// which only works when window scaling is honoured.
    #[tokio::test]
    async fn echo_with_big_segments_and_scaled_window() {
        echo(9000, 1 << 20, 3_000_000).await;
    }

    async fn echo(mtu: usize, buffer: usize, total: u32) {
        let ours: Ipv6Addr = "fd00::2".parse().unwrap();
        let theirs: Ipv6Addr = "fd00::1".parse().unwrap();
        let (stack, mut link_rx) = Stack::start(ours, theirs, 16000);
        let peer_stack = stack.clone();
        tokio::spawn(async move {
            let mut dev = Dev { rx: VecDeque::new(), tx: Vec::new(), mtu };
            let mut iface = Interface::new(Config::new(HardwareAddress::Ip), &mut dev, SInstant::now());
            iface.update_ip_addrs(|a| { a.push(IpCidr::new(IpAddress::Ipv6(theirs), 64)).unwrap(); });
            let mut socket = tcp::Socket::new(
                tcp::SocketBuffer::new(vec![0; buffer]), tcp::SocketBuffer::new(vec![0; buffer]));
            socket.listen(5000).unwrap();
            let mut sockets = SocketSet::new(vec![]);
            let handle = sockets.add(socket);
            loop {
                while let Ok(p) = link_rx.try_recv() {
                    dev.rx.push_back(p);
                }
                iface.poll(SInstant::now(), &mut dev, &mut sockets);
                let s = sockets.get_mut::<tcp::Socket>(handle);
                if s.can_recv() && s.can_send() {
                    let mut chunk = vec![0u8; s.send_capacity() - s.send_queue()];
                    let n = s.recv_slice(&mut chunk).unwrap();
                    s.send_slice(&chunk[..n]).unwrap();
                }
                iface.poll(SInstant::now(), &mut dev, &mut sockets);
                for p in dev.tx.drain(..) {
                    peer_stack.deliver(p);
                }
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        });

        let mut conn = stack.connect(5000).await.expect("connect");
        let data: Vec<u8> = (0..total).map(|i| (i * 7 % 251) as u8).collect();
        let got = {
            let mut sent = 0;
            let mut got = Vec::new();
            while got.len() < data.len() {
                if sent < data.len() {
                    let end = (sent + 16384).min(data.len());
                    conn.write_all(&data[sent..end]).await.unwrap();
                    sent = end;
                }
                if let Ok(Ok(chunk)) = tokio::time::timeout(Duration::from_millis(5), conn.read_some()).await {
                    got.extend(chunk);
                }
            }
            got
        };
        assert_eq!(got.len(), data.len());
        assert!(got == data, "echoed bytes differ");
    }
}
