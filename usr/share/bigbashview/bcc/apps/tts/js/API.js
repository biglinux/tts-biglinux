export class API {
    static setRate(rate) {
        _run(`./scripts/rate.run ${rate}`)
    }

    static setPitch(pitch) {
        _run(`./scripts/pitch.run ${pitch}`)
    }

    static setVolume(volume) {
        _run(`./scripts/volume.run ${volume}`)
    }

    static setVoice(voice) {
        _run(`./scripts/voice.run ${voice}`)
    }
}