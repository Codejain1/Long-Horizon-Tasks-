// The owner's assistant on iPhone (Expo Go). One chat with the bot: replies and progress arrive by long-poll,
// choices are buttons, links open the app they point to, the phone's location goes with each message (once
// allowed), and replies can be read aloud. Talks to bot/appapi.py with a secret key (app/.env.local).
import { useEffect, useRef, useState } from 'react';
import {
  FlatList, KeyboardAvoidingView, Linking, Platform, Pressable, StyleSheet, Switch, Text, TextInput, View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import * as Speech from 'expo-speech';

const URL = process.env.EXPO_PUBLIC_BOT_URL ?? '';
const KEY = process.env.EXPO_PUBLIC_BOT_KEY ?? '';
const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${KEY}` };

type Msg = { id: string; from: 'me' | 'bot'; text: string; choices?: string[] };
type Coords = { lat: number; lng: number } | null;

const LINK = /(https?:\/\/[^\s]+)/g; // for split/replace
const IS_LINK = /^https?:\/\//; // for testing one part (a /g regex's .test() keeps state between calls)

function Body({ text, mine }: { text: string; mine: boolean }) {
  // plain text with tappable links (a Swiggy dish, a maps link, later a UPI payment)
  const parts = text.split(LINK);
  return (
    <Text style={[styles.text, mine && styles.textMine]}>
      {parts.map((p, i) =>
        IS_LINK.test(p) ? (
          <Text key={i} style={styles.link} onPress={() => Linking.openURL(p)}>{p}</Text>
        ) : (
          <Text key={i}>{p}</Text>
        ),
      )}
    </Text>
  );
}

export default function App() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState('');
  const [speak, setSpeak] = useState(false);
  const [online, setOnline] = useState(true);
  const coords = useRef<Coords>(null);
  const after = useRef(0);
  const speakRef = useRef(speak);
  speakRef.current = speak;
  const list = useRef<FlatList<Msg>>(null);

  // location: asked once; refreshed before each message
  const locate = async (): Promise<Coords> => {
    try {
      const { status } = await Location.getForegroundPermissionsAsync();
      if (status !== 'granted') return coords.current;
      const p = await Location.getCurrentPositionAsync({});
      coords.current = { lat: p.coords.latitude, lng: p.coords.longitude };
    } catch {
      // keep the last known position
    }
    return coords.current;
  };

  useEffect(() => {
    Location.requestForegroundPermissionsAsync().then(() => locate());
  }, []);

  // replies, progress and choices: a long-poll that always waits for the next event
  useEffect(() => {
    let alive = true;
    (async () => {
      while (alive) {
        try {
          const r = await fetch(`${URL}/events?after=${after.current}&wait=25`, { headers });
          if (!r.ok) throw new Error(String(r.status));
          const { events } = await r.json();
          setOnline(true);
          if (events.length) {
            after.current = events[events.length - 1].id;
            setMsgs((m) => [
              ...m,
              ...events.map((e: { id: number; text: string; choices?: string[] }) => ({
                id: `b${e.id}`, from: 'bot' as const, text: e.text, choices: e.choices,
              })),
            ]);
            if (speakRef.current) {
              for (const e of events) Speech.speak(e.text.replace(LINK, 'link'), { language: 'en-IN' });
            }
          }
        } catch {
          setOnline(false);
          await new Promise((res) => setTimeout(res, 3000));
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const send = async (text: string) => {
    const t = text.trim();
    if (!t) return;
    setDraft('');
    Speech.stop();
    setMsgs((m) => [...m, { id: `m${Date.now()}`, from: 'me', text: t }]);
    const location = await locate();
    try {
      const r = await fetch(`${URL}/messages`, { method: 'POST', headers, body: JSON.stringify({ text: t, location }) });
      if (!r.ok) throw new Error(String(r.status));
    } catch {
      setMsgs((m) => [...m, { id: `e${Date.now()}`, from: 'bot', text: "Couldn't reach the assistant. Is the tunnel running?" }]);
    }
  };

  const last = msgs[msgs.length - 1];
  return (
    <KeyboardAvoidingView style={styles.screen} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <StatusBar style="dark" />
      <View style={styles.header}>
        <Text style={styles.title}>Assistant</Text>
        <View style={styles.row}>
          {!online && <Text style={styles.offline}>offline</Text>}
          <Text style={styles.small}>Read aloud</Text>
          <Switch value={speak} onValueChange={(v) => { setSpeak(v); if (!v) Speech.stop(); }} />
        </View>
      </View>
      <FlatList
        ref={list}
        data={msgs}
        keyExtractor={(m) => m.id}
        contentContainerStyle={styles.list}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.from === 'me' ? styles.mine : styles.theirs]}>
            <Body text={item.text} mine={item.from === 'me'} />
            {item.choices && item === last && (
              <View style={styles.choices}>
                {item.choices.map((c) => (
                  <Pressable key={c} style={styles.choice} onPress={() => send(c)}>
                    <Text style={styles.choiceText}>{c}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        )}
      />
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask anything… (tap the mic on the keyboard to talk)"
          onSubmitEditing={() => send(draft)}
          returnKeyType="send"
        />
        <Pressable style={styles.send} onPress={() => send(draft)}>
          <Text style={styles.sendText}>Send</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#F4F5F7' },
  header: {
    paddingTop: 58, paddingHorizontal: 16, paddingBottom: 10, backgroundColor: '#fff',
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    borderBottomWidth: StyleSheet.hairlineWidth, borderColor: '#ddd',
  },
  title: { fontSize: 20, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  small: { fontSize: 13, color: '#555' },
  offline: { fontSize: 12, color: '#C0392B', marginRight: 6 },
  list: { padding: 12, gap: 8 },
  bubble: { maxWidth: '86%', padding: 10, borderRadius: 14 },
  mine: { alignSelf: 'flex-end', backgroundColor: '#1F6FEB' },
  theirs: { alignSelf: 'flex-start', backgroundColor: '#fff' },
  text: { fontSize: 15, color: '#111', lineHeight: 21 },
  textMine: { color: '#fff' },
  link: { color: '#1F6FEB', textDecorationLine: 'underline' },
  choices: { marginTop: 8, gap: 6 },
  choice: { borderWidth: 1, borderColor: '#1F6FEB', borderRadius: 10, paddingVertical: 8, paddingHorizontal: 10 },
  choiceText: { color: '#1F6FEB', fontSize: 15, fontWeight: '600' },
  composer: { flexDirection: 'row', padding: 10, paddingBottom: 28, gap: 8, backgroundColor: '#fff' },
  input: { flex: 1, borderWidth: 1, borderColor: '#ddd', borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, fontSize: 15 },
  send: { backgroundColor: '#1F6FEB', borderRadius: 20, paddingHorizontal: 16, justifyContent: 'center' },
  sendText: { color: '#fff', fontWeight: '700' },
});
