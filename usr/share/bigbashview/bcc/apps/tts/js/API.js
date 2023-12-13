export class API {
    static setRate(rate) {
        _run(`./rate.run ${rate}`)
    }

    static setPitch(pitch) {
        _run(`./pitch.run ${pitch}`)
    }

    static setVolume(volume) {
        _run(`./volume.run ${volume}`)
    }

    static setVoice(voice) {
        _run(`./voice.run ${voice}`)
    }
}