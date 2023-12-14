import { API } from "./API.js"

/** @type {HTMLSelectElement} */
const PITCHSelect = document.querySelector('#PITCH')

/** @type {HTMLSelectElement} */
const RATESelect = document.querySelector('#RATE')

/** @type {HTMLSelectElement} */
const VOLUMESelect = document.querySelector('#VOLUME')

/** @type {HTMLSelectElement} */
const VOICESelect = document.querySelector('#VOICE')

PITCHSelect.addEventListener("change", () => {
    API.setPitch(PITCHSelect.value)
})

RATESelect.addEventListener("change", () => {
    API.setRate(RATESelect.value)
})

VOLUMESelect.addEventListener("change", () => {
    API.setVolume(VOLUMESelect.value)
})

VOICESelect.addEventListener("change", () => {
    API.setVoice(VOICESelect.value)
})