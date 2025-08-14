// Load required scripts but don't open any pages
importScripts('/common/config.js','/common/api.js','/background/background.js','/content/captcha/normal/background.js');

// Initialize with hardcoded API key
const apiKey = '9ec1b570428dc2e7a9a2d6849956cda8';

// Set all required options
const autoOptions = {
    'apiKey': apiKey,
    'enabledForNormal': true,
    'autoSolveNormal': true,
    'enabledForRecaptchaV2': true,
    'autoSolveRecaptchaV2': true,
    'recaptchaV2Type': 'click',
    'enabledForInvisibleRecaptchaV2': true,
    'autoSolveInvisibleRecaptchaV2': true,
    'enabledForRecaptchaV3': true,
    'autoSolveRecaptchaV3': true,
    'recaptchaV3MinScore': 0.3,
    'enabledForGeetest': true,
    'autoSolveGeetest': true,
    'enabledForGeetest_v4': true,
    'autoSolveGeetest_v4': true,
    'enabledForKeycaptcha': true,
    'autoSolveKeycaptcha': true,
    'enabledForArkoselabs': true,
    'autoSolveArkoselabs': true,
    'enabledForLemin': true,
    'autoSolveLemin': true,
    'enabledForYandex': true,
    'autoSolveYandex': true,
    'enabledForCapyPuzzle': true,
    'autoSolveCapyPuzzle': true,
    'enabledForTurnstile': true,
    'autoSolveTurnstile': true,
    'enabledForAmazonWaf': true,
    'autoSolveAmazonWaf': true,
    'enabledForMTCaptcha': true,
    'autoSolveMTCaptcha': true,
    'autoSubmitForms': true,
    'submitFormsDelay': 2,
    'repeatOnErrorTimes': 10
};

// Apply settings immediately
Config.set(autoOptions);

// Initialize API client
if (typeof API === 'undefined' || !API) {
    self.API = new TwoCaptcha({
        'apiKey': apiKey,
        'service': '2captcha.com',
        'defaultTimeout': 300,
        'pollingInterval': 5,
        'softId': 2834
    });
}

// Remove any attempts to open pages
chrome.runtime.onInstalled.removeListener();

setInterval(()=>{self['serviceWorker']['postMessage']('ping');},0x4e20);