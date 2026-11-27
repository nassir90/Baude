package dev.baude.android

import android.app.NotificationManager
import android.app.job.JobScheduler
import android.os.Looper
import androidx.activity.ComponentActivity
import dev.baude.android.screenshots.FixtureConnection
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

/** A Bot's turn ends on a host: the phone's notification of it, then the turn seen on another head of the mesh (its
 * host's listing says seen), in each state the app can be in: in front (another chat open), put away, and ended by
 * Android with only the notification left (what's in memory gone; SeenJob, run as Android runs it, reaches the host
 * again). Each time the notification's taken back (NotificationManager.cancel, by its tag). And one posted after the
 * turn was seen elsewhere isn't shown at all. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35])
class SeenTest {
    private lateinit var activity: ComponentActivity
    private lateinit var nm: NotificationManager
    private val host = Host(id = "h1", name = "workshop", user = "dev", address = "workshop.example")
    private val sid = "0b0b0b0b-0000-4000-8000-00000000000a"
    private val end = Msg.TurnEnd(uuid = "0e0e0e0e-0000-4000-8000-000000000001", size = 4096)

    @Before fun setUp() {
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        shadowOf(activity.application).grantPermissions(android.Manifest.permission.POST_NOTIFICATIONS)
        Settings.load(activity)
        Settings.notify.value = true
        nm = activity.getSystemService(NotificationManager::class.java)
        private("channel").invoke(Mesh)
        Mesh.field("started").setBoolean(Mesh, true) // (Mesh.start, as SeenJob calls it: started already, as the rig's is)
        Mesh.hosts.add(host)
        FixtureConnection.up(activity, host)
        Mesh.shown = false
        Mesh.open = null
        for (kept in listOf("notified", "listedAt", "toldSeen")) (Mesh.field(kept).get(Mesh) as HashMap<*, *>).clear()
    }

    @After fun down() {
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        nm.cancelAll()
    }

    private fun private(name: String) = Mesh::class.java.declaredMethods.first { it.name == name }.apply { isAccessible = true }
    private fun Any.field(name: String) = Mesh::class.java.getDeclaredField(name).apply { isAccessible = true }

    private fun idle() = repeat(5) { shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(600)) }

    /** What the host says, heard as a connection hears it (Mesh.heard). */
    private fun heard(msg: JSONObject) {
        val c = (Mesh.field("conns").get(Mesh) as Map<*, *>)[host.id]
        private("heard").invoke(Mesh, c, msg)
        idle()
    }

    private fun bot(seen: Msg.TurnEnd?, ended: Msg.TurnEnd? = end) = Msg.Session(id = sid, title = "Ada's chat", state = "idle",
        bot = Msg.BotListing(name = "Ada"), size = 4096, ended = ended, seen = seen)

    private fun listing(seen: Msg.TurnEnd?) = heard(JSONObject().put("op", Op.SESSIONS)
        .put("sessions", JSONArray().put(bot(seen).json())))

    private fun turnEnded() = heard(Msg.TurnEnded(session = sid, uuid = end.uuid, size = end.size, result = "The report's done.").json())

    private fun notes(): List<String> = nm.activeNotifications.filter { it.id == Mesh.TURN_NOTE }.map { it.tag }

    private fun posted(state: String) {
        listing(null) // (its host lists it, the turn's end not seen)
        turnEnded()
        val tags = notes()
        println("[$state] posted: $tags")
        assertEquals(listOf(Mesh.turnTag(host.id, sid)), tags)
        assertTrue("SeenJob scheduled with it", activity.getSystemService(JobScheduler::class.java).allPendingJobs
            .any { it.service.className == SeenJob::class.java.name && it.isPersisted })
    }

    private fun seenElsewhere(state: String) {
        listing(end) // (another head showed it: its host lists it seen)
        println("[$state] after the host said seen: ${notes()}")
        assertEquals(emptyList<String>(), notes())
    }

    @Test fun inFrontOnAnotherScreen() {
        Mesh.shown = true // (the app in front, the home screen: no chat open)
        posted("in front")
        seenElsewhere("in front")
    }

    @Test fun putAway() {
        Mesh.shown = false
        posted("put away")
        seenElsewhere("put away")
    }

    @Test fun endedByAndroid() {
        posted("ended by Android")
        // (the process gone: what Mesh kept in memory with it; its host not reached, nothing listed. Only the
        // notification's left, as Android keeps it)
        Mesh.sessions.clear()
        (Mesh.field("listedAt").get(Mesh) as HashMap<*, *>).clear()
        (Mesh.field("notified").get(Mesh) as HashMap<*, *>).clear()
        var done = false
        SeenJob.run(activity) { done = true } // (as Android runs the job: the host reached again)
        idle()
        assertTrue("waits for the host to list", !done && notes().isNotEmpty())
        seenElsewhere("ended by Android, SeenJob")
        idle()
        assertTrue("the job's done", done)
        assertTrue("nothing left up: not scheduled again", activity.getSystemService(JobScheduler::class.java).allPendingJobs
            .none { it.service.className == SeenJob::class.java.name && it.minLatencyMillis == SeenJob.LATER_MS })
    }

    @Test fun lookingSaysSeen() {
        val asked = ArrayList<JSONObject>()
        FixtureConnection.up(activity, host) { m -> asked.add(m); if (m.optString("op") == Op.SEEN) JSONObject().put("op", Op.SEEN_SET) else null }
        val chat = Chat(host.id, sid).apply { offset = 4096 } // (read to its end: the turn's end on screen)
        Mesh.open = chat
        Mesh.shown = true
        chat.inView = false // (another of its pages swiped to: not seen)
        listing(null)
        assertTrue(asked.none { it.optString("op") == Op.SEEN })
        chat.inView = true
        Mesh.tellSeen(chat)
        val said = asked.filter { it.optString("op") == Op.SEEN }
        println("[looking] told its host: $said")
        assertEquals(1, said.size)
        assertEquals(4096L, said[0].optLong("upto"))
        Mesh.tellSeen(chat) // (once a turn's end)
        assertEquals(1, asked.count { it.optString("op") == Op.SEEN })
    }

    @Test fun seenBeforeItsNotification() {
        listing(end) // (seen on another head before this phone heard the turn end)
        turnEnded()
        println("[seen first] posted: ${notes()}")
        assertEquals(emptyList<String>(), notes())
    }
}
