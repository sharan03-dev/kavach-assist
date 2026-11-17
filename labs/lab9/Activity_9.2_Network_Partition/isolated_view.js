// Lab 9.2 - rs.status() from INSIDE a member.
// `docker exec` does not use the Docker network, so this works even while
// the container is disconnected:
//   docker exec -i mongo3 mongosh --port 27019 --quiet < isolated_view.js
const st = rs.status();
const me = st.members.find(m => m.self);
print(`rs.status() as seen by ${me.name} (term ${st.term}) at ${new Date().toISOString()}`);
st.members.forEach(m => print(
  `  ${m.name.padEnd(14)} ${m.stateStr.padEnd(26)} health=${m.health}` +
  (m.self ? "   <- self" : (m.lastHeartbeatMessage ? `   (${m.lastHeartbeatMessage.slice(0, 70)})` : ""))));
const p = st.members.find(m => m.stateStr === "PRIMARY");
print(p ? `  -> sees PRIMARY ${p.name}`
        : "  -> sees NO PRIMARY: it cannot reach a majority, so it cannot elect itself");
