// The owner's assistant on iPhone (Expo Go). One chat with the bot: replies and progress arrive by long-poll,
// choices are buttons, links open the app they point to, the phone's location goes with each message (once
// allowed), and replies can be read aloud. Talks to bot/appapi.py with a secret key (app/.env.local).
import { useEffect, useRef, useState } from 'react';
import {
  FlatList, Image, KeyboardAvoidingView, Linking, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Location from 'expo-location';
import * as Speech from 'expo-speech';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder } from 'expo-audio';

const URL = process.env.EXPO_PUBLIC_BOT_URL ?? '';
const KEY = process.env.EXPO_PUBLIC_BOT_KEY ?? '';
const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${KEY}` };

type Card = {
  choice: string; title: string; subtitle?: string; price?: string; rating?: string; image?: string; note?: string;
  veg?: boolean | null;
};
type Msg = { id: string; from: 'me' | 'bot'; text: string; choices?: string[]; cards?: Card[] };
type Coords = { lat: number; lng: number } | null;

const LINK = /(https?:\/\/[^\s]+)/g; // for split/replace
// one tap to start: what the bot does well today (food by dish + budget, ideas from your orders, your location)
const SUGGESTIONS = ['What should I eat?', 'Biryani under 300', 'Paneer wrap under 250', 'Masala dosa under 150', 'Where am I?'];
const WORKING = /…$/; // the bot's progress notes ("Comparing … with reviews…"): the answer is still coming
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
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [listening, setListening] = useState(false);
  const [waiting, setWaiting] = useState(false); // sent, no answer yet: show that the bot is working
  const [dots, setDots] = useState('');

  useEffect(() => {
    if (!waiting) return;
    const tick = setInterval(() => setDots((d) => (d.length >= 3 ? '' : d + '.')), 400);
    const giveUp = setTimeout(() => setWaiting(false), 90000); // never spin forever
    return () => { clearInterval(tick); clearTimeout(giveUp); };
  }, [waiting]);

  // hold to talk: record while pressed, then the server turns it into text (Whisper) and handles it like typing
  const startTalk = async () => {
    try {
      const perm = await AudioModule.requestRecordingPermissionsAsync();
      if (!perm.granted) return;
      Speech.stop();
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      setListening(true);
    } catch {
      setListening(false);
    }
  };

  const stopTalk = async () => {
    if (!listening) return;
    setListening(false);
    try {
      await recorder.stop();
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }); // replies play on the speaker
      const uri = recorder.uri;
      if (!uri) return;
      setWaiting(true);
      const audio = await (await fetch(uri)).blob();
      const loc = await locate();
      const q = loc ? `?lat=${loc.lat}&lng=${loc.lng}` : '';
      const r = await fetch(`${URL}/voice${q}`, {
        method: 'POST', headers: { Authorization: `Bearer ${KEY}`, 'Content-Type': 'audio/mp4' }, body: audio,
      });
      const { text } = await r.json();
      setMsgs((m) => [...m, { id: `v${Date.now()}`, from: 'me', text: text ? `🎤 ${text}` : "🎤 (didn't catch that, try again)" }]);
    } catch {
      setWaiting(false);
      setMsgs((m) => [...m, { id: `e${Date.now()}`, from: 'bot', text: "Couldn't send the voice note. Is the tunnel running?" }]);
    }
  };

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
            setWaiting(WORKING.test(events[events.length - 1].text.trim()));
            setMsgs((m) => [
              ...m,
              ...events.map((e: { id: number; text: string; choices?: string[]; cards?: Card[] }) => ({
                id: `b${e.id}`, from: 'bot' as const, text: e.text, choices: e.choices, cards: e.cards,
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
    setWaiting(true);
    const location = await locate();
    try {
      const r = await fetch(`${URL}/messages`, { method: 'POST', headers, body: JSON.stringify({ text: t, location }) });
      if (!r.ok) throw new Error(String(r.status));
    } catch {
      setWaiting(false);
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
        ListHeaderComponent={
          <View style={[styles.bubble, styles.theirs]}>
            <Text style={styles.text}>
              Hi! Tell me a dish and a budget and I'll compare Swiggy, magicpin and EatSure near you. Tap an idea
              below, type, or hold 🎤 to talk.
            </Text>
          </View>
        }
        ListFooterComponent={
          waiting ? (
            <View style={[styles.bubble, styles.theirs, styles.typing]}>
              <Text style={styles.typingText}>Working on it{dots}</Text>
            </View>
          ) : undefined
        }
        renderItem={({ item }) => (
          <View style={[styles.bubble, item.from === 'me' ? styles.mine : styles.theirs]}>
            {item.cards?.length ? (
              <>
                <Text style={styles.text}>{item.text.split('\n')[0]}</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.cards}>
                  {item.cards.map((c) => (
                    <Pressable key={c.choice} style={({ pressed }) => [styles.card, pressed && styles.pressed]}
                      onPress={() => send(c.choice)}>
                      {c.image ? <Image source={{ uri: c.image }} style={styles.cardImage} /> : <View style={styles.cardImage} />}
                      <View style={styles.cardBody}>
                        <Text style={styles.cardTitle} numberOfLines={2}>
                          {c.veg === true ? '🟢 ' : c.veg === false ? '🔴 ' : ''}{c.title}
                        </Text>
                        {!!c.subtitle && <Text style={styles.cardSub} numberOfLines={1}>{c.subtitle}</Text>}
                        <Text style={styles.cardPrice}>
                          {c.price}{c.rating ? `  ★ ${c.rating}` : ''}
                        </Text>
                        {!!c.note && <Text style={styles.cardNote} numberOfLines={2}>{c.note}</Text>}
                      </View>
                    </Pressable>
                  ))}
                </ScrollView>
              </>
            ) : (
              <Body text={item.text} mine={item.from === 'me'} />
            )}
            {item.choices && !item.cards?.length && item === last && (
              <View style={styles.choices}>
                {item.choices.map((c) => (
                  <Pressable key={c} style={({ pressed }) => [styles.choice, pressed && styles.pressed]}
                    onPress={() => send(c)}>
                    <Text style={styles.choiceText}>{c}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        )}
      />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chips}
        contentContainerStyle={styles.chipsRow} keyboardShouldPersistTaps="handled">
        {SUGGESTIONS.map((q) => (
          <Pressable key={q} style={({ pressed }) => [styles.chip, pressed && styles.pressed]} onPress={() => send(q)}>
            <Text style={styles.chipText}>{q}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={draft}
          onChangeText={setDraft}
          placeholder="Ask anything, or hold 🎤 to talk"
          onSubmitEditing={() => send(draft)}
          returnKeyType="send"
        />
        {draft.trim() ? (
          <Pressable style={styles.send} onPress={() => send(draft)}>
            <Text style={styles.sendText}>Send</Text>
          </Pressable>
        ) : (
          <Pressable style={[styles.send, listening && styles.listening]} onPressIn={startTalk} onPressOut={stopTalk}>
            <Text style={styles.sendText}>{listening ? 'Listening…' : '🎤 Hold'}</Text>
          </Pressable>
        )}
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
  bubble: { maxWidth: '92%', padding: 10, borderRadius: 14 },
  mine: { alignSelf: 'flex-end', backgroundColor: '#1F6FEB' },
  theirs: { alignSelf: 'flex-start', backgroundColor: '#fff' },
  text: { fontSize: 15, color: '#111', lineHeight: 21 },
  textMine: { color: '#fff' },
  link: { color: '#1F6FEB', textDecorationLine: 'underline' },
  choices: { marginTop: 8, gap: 6 },
  cards: { marginTop: 8, marginHorizontal: -4 },
  card: { width: 190, marginHorizontal: 4, borderRadius: 12, backgroundColor: '#fff', borderWidth: 1, borderColor: '#e3e3e3', overflow: 'hidden' },
  cardImage: { width: 190, height: 110, backgroundColor: '#eee' },
  cardBody: { padding: 8, gap: 2 },
  cardTitle: { fontSize: 14, fontWeight: '700', color: '#111' },
  cardSub: { fontSize: 12, color: '#666' },
  cardPrice: { fontSize: 14, fontWeight: '700', color: '#1F6FEB', marginTop: 2 },
  cardNote: { fontSize: 11, color: '#555' },
  choice: { borderWidth: 1, borderColor: '#1F6FEB', borderRadius: 10, paddingVertical: 8, paddingHorizontal: 10 },
  choiceText: { color: '#1F6FEB', fontSize: 15, fontWeight: '600' },
  composer: { flexDirection: 'row', padding: 10, paddingBottom: 28, gap: 8, backgroundColor: '#fff' },
  input: { flex: 1, borderWidth: 1, borderColor: '#ddd', borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, fontSize: 15 },
  send: { backgroundColor: '#1F6FEB', borderRadius: 20, paddingHorizontal: 16, justifyContent: 'center' },
  sendText: { color: '#fff', fontWeight: '700' },
  listening: { backgroundColor: '#D93025' },
  pressed: { opacity: 0.55 },
  typing: { backgroundColor: '#E9EEF6' },
  typingText: { fontSize: 15, color: '#555', fontStyle: 'italic' },
  chips: { flexGrow: 0, backgroundColor: '#fff', borderTopWidth: StyleSheet.hairlineWidth, borderColor: '#ddd' },
  chipsRow: { paddingHorizontal: 10, paddingTop: 8, gap: 8 },
  chip: { borderRadius: 16, backgroundColor: '#EEF3FC', paddingVertical: 7, paddingHorizontal: 12 },
  chipText: { color: '#1F6FEB', fontSize: 14, fontWeight: '600' },
});
