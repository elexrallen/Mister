// Parse translation strings before anything else
var resources = {};
resources[_FG_cfg.language.code] = { 'translation': i18n };

// i18next to handle translations in ICU format in the frontend
i18next
  .use(i18nextICU)
  .init({
    lng: _FG_cfg.language.code,
    resources: resources
  });

$.ajaxSetup({
  type: 'POST',
  headers: { 'X-Auth': _FG_cfg.auth },
  timeout: 30000,
  error: function (response) {
    showAjaxErrorToast(response);
  }
});

var popup = false;
var popup_content;

var swOpened = false;

var scroll_pos = 0;

var toast_open = false;
var toast_timeout;
var undo = false;

var lock = false;

var sws = {};

var currentPath = window.location.pathname;

sws['settings/notifications'] = {
  post: 'notifications'
};

sws.progression = {
  post: 'progression'
};

sws.store = {
  post: {
    post: 'store',
    coupon: null
  }
};

sws.subs = {
  post: {
    post: 'subs'
  }
};

sws.balance = {
  post: 'balance'
};

_FG_data.market = {
  post: 'market',
  interval: 'day'
};

sws.market = {
  post: _FG_data.market
};

if (typeof _FG_user !== 'undefined') {
  sws.competition = {
    post: 'competition'
  };
}

var btn_purchase;

var currentPromo = null;

setTimeout(function () {
  sendPendingPurchaseEvents();
}, 2000);

var paypalStoreKey = 'paypal_purchases';

var purchaseAttemps = 0;

var initialCredits = null;
var initialCash    = null;

var attachedScrolls = {};

var eventSeenObservers = [];

_FG_cfg.active_tabs = {};

var btn_pools;

MutationObserver = window.MutationObserver || window.WebKitMutationObserver;

var observer_input_range = new MutationObserver(function (mutations) {
  var input = $(mutations[0].target).find('input[type=range]:not(.range-dual)');
  input.each(function(i) {
    update_range_bg($(this).get(0));
  });
});

observer_input_range.observe(document.getElementById('popup-content'), {
  childList: true
});

var observer_local_time = new MutationObserver(function (mutations) {
  var items = $(mutations[0].target).find('.tz');
  if (items.length > 0) {
    local_time();
  }
});

observer_local_time.observe(document.body, {
  childList: true,
  subtree: true
});

var giphyText;
var giphyLoading = false;
var giphyOffset = 0;

var tutorialPopup = false;
var tutorialTooltip = false;

var eventsPerStep = {
  1: '_tutorial_seen',
  2: '_tutorial_began',
};

var pressed = false;
var speed = 201;
var running = function (action, step, increment) {
  if (pressed) {
    input_value(action, step, increment);
    if (step == 10) {
      setTimeout(function () {
        running(action, step, increment);
      }, speed);
      increment = increment + step;
      if (speed > 1) {
        speed = speed - 20;
      }
    }
  }
};

var ratingPrompt = $('.app-rating-prompt');
var ratingPromptText;
var ratingPromptTextArray;
var ratingPromptButtons;
var ratingPromptInterval;

if (ratingPrompt.length > 0) {
  var ratingPromptStore = _FG_cfg.app == 'ios' ? 'Apple Store' : 'Google Play';
  ratingPromptText = trans('RATING_PROMPT_INITIAL_MESSAGE', { brand_name: _FG_cfg.brand.name });
  ratingPromptButtons = '.app-rating-mood';
  setTimeout(function() {
    ratingPrompt.addClass('show');
    writeRatingPromptBubble();
  }, 500);
}


if (_FG_cfg.app == 'ios') {
  window.webkit.messageHandlers.getToken.postMessage(1);
} else if (_FG_cfg.app == 'android') {
  if (typeof MRInterface.getFirebaseToken !== 'undefined') {
    var token = MRInterface.getFirebaseToken();
    if (_FG_user.tokens.indexOf(token) < 0 && typeof token !== 'undefined') {
      ntf_subscribe(token);
    }
  }
}

// Ask for Notifications permissions for Android 13
if (_FG_cfg.app === "android" && typeof Permissions !== "undefined") {
  try {
    window.onAndroidRequestPermission = (error, result) => {
      if (error) {
        console.error(`Error requesting notifications permission: `, error);
        return;
      }

      if (result.isGranted) {
        console.log("User granted notifications permission");
        return;
      }

      // Here we just depend on the launchAppSettings method...
    };

    window.onAndroidShouldShowPermissionRationale = (error, result) => {
      if (error) {
        console.error(`Error trying to check for notifications permission rationale: `, error);
        return;
      }

      if (result.showRationale) {
        console.log("Android notifications permission rationale => show UI");
        // Here we just depend on the launchAppSettings method...
        return;
      }

      Permissions.requestPermission(JSON.stringify({
        "permission": "android.permission.POST_NOTIFICATIONS",
        "callback": "onAndroidRequestPermission"
      }));
    };

    window.onAndroidPermissionGrantedCallback = (error, result) => {
      if (error) {
        console.error(`Error trying to check for notifications permission: `, error);
        return;
      }

      if (result.isGranted) {
        console.log("Android Notifications Permission is granted");
        return;
      }

      Permissions.shouldShowPermissionRationale(JSON.stringify({
        "permission": "android.permission.POST_NOTIFICATIONS",
        "callback": "onAndroidShouldShowPermissionRationale"
      }));
    };

    Permissions.isPermissionGranted(JSON.stringify({
      "permission": "android.permission.POST_NOTIFICATIONS",
      "callback": "onAndroidPermissionGrantedCallback"
    }));
  } catch (e) {}
}

var lastForegroundTimestamp;

runAfterPageLoad();

setInterval(function() {
  updateBalance();
  checkCommunity();
}, 1000 * 300);

var popupData = null;

if (_FG_cfg.update_last_seen) {
  // We are not synchronizing script loading so we have added this
  // 5s delay. Also updating the last sen is not as important as other
  // requests so it is ok to delay it.
  setTimeout(updateLastSeen, 5000);
}

/**
 * Coupons
 */
const AUTOMATIC_REDEEM_COUPON_REDIRECT_PATH = "/feed";
const AUTOMATIC_REDEEM_COUPON_REDIRECT_HASH = "#store";
const AUTOMATIC_REDEEM_COUPON_REDIRECT_QUERY_PARAM_NAME = "coupon";

initializeAutomaticCouponRedeemingRedirection();

sws['admin/tools/cancel-transfers'] = {
  post : 'cancel-transfers'
};

sws['users-balances'] = {
  post : 'users-balances'
};

var req_leave = {
  admin: false,
  check: false
};

updateNativeBarsColor();

if (showGoodbyeAdsCta()) {
  popup_open('goodbye-ads');
}

if (showRevolutPromo()) {
  toast(trans('REVOLUT_PROMO_TOAST'), 'blue', 5, null, () => popup_open('revolut'));
  Cookies.set('revolut-promo-seen', 1, {expires: 1});
}
