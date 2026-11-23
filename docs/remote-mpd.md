# Network MPD output

Tunes can play through an MPD server on another computer. Tunes keeps the library
and the queue, and sends MPD one track at a time. MPD decodes and plays on that
computer. The DAC, any DSP, and the volume behind MPD are set up there. Tunes
does not configure them.

## In Tunes

Add the server under **Settings → Audio → Network outputs**: a name, a host, and
a port (6600 unless MPD is on another port). **Test connection** should report
an MPD version. The output then appears in **Output device** as *name (network
MPD)*.

Several servers can be saved. One of them is active at a time. Choosing a local
device again leaves the servers in the list.

While a network output is active:

- The Tunes volume slider, volume keys, and MPRIS volume do nothing. Set the
  level on the endpoint. Tunes does not claim bit-perfect playback; the playback
  note says "via MPD (name)".
- Files from the local library are handed to MPD as `http://` URLs served by
  Tunes. The MPD computer must be able to connect back to this one. Qobuz and
  TIDAL URLs are fetched by MPD itself.
- TIDAL is limited to the lossless URL tier. Hi-res TIDAL is a local DASH
  manifest, and a network MPD cannot open that file.
- If another program already has the sound device open, Tunes reports the output
  as busy. MPD does not retry. Press play again after that program lets go.

## On the MPD computer

MPD has to be reachable on the LAN, and it has to open the device you actually
want to hear.

**Listening.** From the Tunes computer, port 6600 must accept a connection. On
current Debian, `mpd.socket` does that with `ListenStream=6600`, which is every
interface, and `bind_to_address` in `mpd.conf` is ignored while that socket is
in use. If nothing is listening, or the socket is bound to localhost only, set
`bind_to_address "any"` and run `mpd.service` without the socket unit.

**The device.** Name it by the card id from `/proc/asound/cards`, for example
`hw:CARD=DAC,DEV=0`. A card number such as `hw:0,0` can point at a different
card after a reboot.

**Volume.** If a knob outside MPD sets the level, MPD's mixer is `none`.
`replaygain` stays `"off"`.

**Sharing the device.** A device with one playback subdevice can be open for
only one program. MPD closes it on pause when `close_on_pause` is `"yes"`,
which is already the default. The other program has to release the device when
it is not playing, too.

**Sample rate.** Tunes sends the stream as it is. MPD should change the bit
depth to what the DAC accepts and leave the rate alone, unless the path after
MPD can play only one rate. Those are two different `allowed_formats` settings.

### The DAC accepts the file's rate

A USB DAC that plays PCM as `S32_LE` at each of its own rates wants one wildcard
entry. `*:32:2` means any rate, 32-bit, stereo. MPD pads 16-bit and 24-bit
samples to 32-bit and does not resample.

```
audio_output {
        type            "alsa"
        name            "DAC"
        device          "hw:CARD=DAC,DEV=0"
        mixer_type      "none"
        auto_resample   "no"
        auto_channels   "no"
        auto_format     "no"
        close_on_pause  "yes"
        allowed_formats "*:32:2"
}
```

`auto_resample "no"` keeps ALSA from resampling a second time. A rate the DAC
cannot play fails, instead of being converted to some other rate.

A list of concrete rates (`44100:32:2 48000:32:2 …`) does not select the file's
rate. MPD uses an entry only when the rate, the bit depth, and the channel
count all match. A 16-bit file matches none of those 32-bit entries, so MPD
falls back to the first entry and plays everything at that rate. A 44.1 kHz
test file hides this, because the fallback and the file are the same rate.

### The path after MPD accepts only one rate

A loopback captured by a DSP at a fixed rate is the other case. Every file has
to become that one format before it reaches the device, so the list contains
that single format and the resampler does the rate change.

```
audio_output {
        type            "alsa"
        name            "To DSP"
        device          "hw:Loopback,0,0"
        mixer_type      "none"
        auto_resample   "no"
        auto_channels   "no"
        auto_format     "no"
        close_on_pause  "yes"
        allowed_formats "192000:32:2"
}
resampler {
        plugin          "soxr"
        quality         "very high"
}
```

Change the device, rate, and bit depth to the format that path actually
accepts.

## Check

While a track is playing, the card's `hw_params` shows the rate and format the
device opened:

```bash
cat /proc/asound/cardN/pcm0p/sub0/hw_params
```

For a DAC in the first setup, a 96 kHz file shows `rate: 96000` and
`format: S32_LE`. After stop, the subdevice status is `closed`. With
`mixer_type "none"`, the ALSA mixer value is the same as before playback.
