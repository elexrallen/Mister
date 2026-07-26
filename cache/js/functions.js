jQuery.event.special.touchstart = {
  setup: function( _, ns, handle ){
    this.addEventListener("touchstart", handle, { passive: true });
  }
};

jQuery.event.special.touchmove = {
  setup: function( _, ns, handle ){
    this.addEventListener("touchmove", handle, { passive: true });
  }
};

jQuery.event.special.touchend = {
  setup: function( _, ns, handle ){
    this.addEventListener("touchend", handle, { passive: true });
  }
};

function popup_show(view, output, element) {
  $('#popup-content').html(output);
  var credits = ['clause-set', 'credits-get', 'credits-redeem', 'formation', 'rescind', 'settings', 'substitution', 'pools-info'];
  if (credits.indexOf(view) > -1 || $(output).find('.btn-credits').length) {
    showStoreFloatingBtn();
  }
  if (!popup) {
    popup = true;
    $('#popup-content').css('max-height', $(window).height() * 0.9 + 'px');
    $('html').addClass('popup-open no-ptr').css('overflow', 'hidden');
    $('#overlay').css('display', 'flex').width();
    $('#overlay').addClass('show');
  }
  if (element) {
    element.removeClass('loading');
  }
  if (typeof window['open_' + view.replace(/-|\//g, '_')] === 'function') {
    window['open_' + view.replace(/-|\//g, '_')]();
  }
}

function popup_twig(view, post, element) {
  var templateData = Object.assign({}, post);
  templateData.cfg = _FG_cfg;
  templateData.user = _FG_user;
  templateData.data = _FG_data;
  var inline = view.replace(/\//g, '-');
  var output;
  if ($('#twig-' + inline).length) {
    output = Twig.twig({
      data: $('#twig-' + inline).text(),
    }).render(templateData);
    popup_show(view, output, element);
  } else {
    template = Twig.twig({
      href: _FG_cfg.paths.views + '/ajax/' + view + '.twig?' + _FG_cfg.twig,
      async: true,
      load: function (template) {
        output = template.render(templateData);
        popup_show(view, output, element);
      }
    });
  }
}

function popup_open(view, post, element, preload) {
  if (element) {
    element.addClass('loading');
  }
  if (preload) {
    $.ajax({
      url: 'ajax/' + preload,
      data: element.get(0).dataset,
      success: function (response) {
        if (preload != 'players') {
          popupData = response.data;
        }
        post.pre = response.data;
        popup_twig(view, post, element);
      }
    });
  } else {
    popup_twig(view, post, element);
  }
}

function popup_back() {
  $('#popup-content').remove();
  $('#popup').append(popup_content);
  popup_content = '';
  btn_unlock($('.popup-navigate'));
  btn_unlock($('.loading'));
  $('.tap').removeClass('tap');
}

function popup_close() {
  popup = false;
  popupData = null;
  $('html').removeClass('popup-open no-ptr').css('overflow', '');
  $('#overlay').removeClass('show');
  hideStoreFloatingBtn();
  $('.live-balance-maxdebt').removeClass('pulse');
  setTimeout(function () {
    $('#overlay').hide();
    $('#popup-content').empty();
    $('.live-balance-top').removeClass('show');
    $('html').removeClass('live-balance-top-show');
  }, 110);
}

function callback_sw_store(post) {
  if (typeof sws.store.extra !== 'undefined') {
    $('.panel-mobile .sku-' + sws.store.extra.click).trigger('click');
  }
}

function sw_twig(div, view, post) {
  var inline = view.replace(/\//g, '-');
  var template;
  var templateData = Object.assign({}, post);
  templateData.cfg = _FG_cfg;
  templateData.user = _FG_user;
  templateData.data = _FG_data;
  if ($('#twig-' + inline).length) {
    template = Twig.twig({
      data: $('#twig-' + inline).text(),
    }).render(templateData);
    div.find('.sw-content').html(template);
    div.find('.sw-content').removeClass('hide');
    div.find('.sw-spinner').removeClass('show loading');
    div.scrollTop(0);
    if (typeof window['callback_sw_' + view.replace(/-|\//g, '_')] === 'function') {
      window['callback_sw_' + view.replace(/-|\//g, '_')](templateData);
    }
    swOpened = true;
  } else {
    template = Twig.twig({
      href: _FG_cfg.paths.views + '/ajax/' + view + '.twig?' + _FG_cfg.twig,
      async: true,
      load: function (template) {
        var output = template.render(templateData);
        div.find('.sw-content').html(output);
        div.find('.sw-content').removeClass('hide');
        div.find('.sw-spinner').removeClass('show loading');
        if (view == 'gameweek' && templateData.id_match > 0) {
          var topSpace = $('.sw-topbar').height() + parseInt(getCSSVar('--spacing-outer'));
          div.scrollTop($('#gameweek-match-' + templateData.id_match).offset().top - topSpace);
        } else {
          div.scrollTop(0);
        }
        if ((div.find('.btn-credits').length && view != 'team/substitution') || view == 'store') {
          showStoreFloatingBtn();
        }
        if (typeof window['callback_sw_' + view.replace(/-|\//g, '_')] === 'function') {
          window['callback_sw_' + view.replace(/-|\//g, '_')](templateData);
        }
        swOpened = true;
      }
    });
  }
}

function sw_open(view, post = null) {
  if (scroll_pos === 0) {
    scroll_pos = document.documentElement.scrollTop || document.body.scrollTop;
  }
  var div = $('.sw');
  div.find('.sw-content').addClass('hide');
  div.find('.sw-spinner').addClass('show loading');
  div.show();
  $('html').addClass('sw-open');
  if (post) {
    var ajaxUrl = 'ajax/sw/' + ((typeof post === 'object') ? post.post : post);
    $.ajax({
      url: ajaxUrl,
      data: (typeof post === 'object') ? post : 'post=' + post,
      success: function (response) {
        if (view.indexOf('admin') === 0) {
          _FG_data.admin = response.data;
        }
        sw_twig(div, view, response.data);
      },
      error: function(response) {
        showAjaxErrorToast(response);
        history.back();
      }
    });
  } else {
    sw_twig(div, view, {});
  }
}

function sw_close() {
  $('html').removeClass('sw-open');
  document.documentElement.scrollTop = document.body.scrollTop = scroll_pos;
  scroll_pos = 0;
  $('.sw-title').empty();
  $('.sw-content').empty();
  $('.sw').hide();
  hideStoreFloatingBtn();
  swOpened = false;
}

function reloadSW() {
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function aPage(page) {
  if (typeof COMSCORE !== "undefined") {
    (self.COMSCORE && COMSCORE.beacon({c1: '2', c2: _FG_cfg.comscoreID}));
  }
  if (typeof dataLayer !== "undefined") {
    console.log("Sending GTM push");
    var r = dataLayer.push({
      'event':'virtualPageView',
      'virtualPageURL': page,
      'virtualPageTitle' : document.title + " " + page
    });
    console.log("GTM result: " + r);
  }
  if (typeof agent !== "undefined") {
    customParams.c2 = window.location.href.substring(window.location.origin.length);
    agent.impression("default", customParams);
  }
  if (typeof gtag !== 'undefined') {
    gtag('event', 'page_view', {
      page_title: document.title,
      page_location: page
    });
  }
}

function toast(text, color, duration = 5, extraButton, onClick = null) {
  clearTimeout(toast_timeout);
  if (extraButton == 'undo') {
    text = text + '. <button class="btn btn-undo">' + trans('Deshacer cambio') + '</button>';
  } else if (extraButton == 'refresh') {
    text = text + '. <button class="btn btn-refresh">' + trans('Actualizar') + '</button>';
  }
  var div = $('#toast');
  div.removeClass('toast-red toast-green flash');
  div.html('<span>' + text + '</span>');
  div.addClass('show');
  if (typeof color === 'string') {
    div.addClass('toast-' + color);
  }
  if (toast_open) {
    div.addClass('flash');
  }
  toast_open = true;
  toast_timeout = setTimeout(function () {
    toast_close();
  }, duration * 1000);
  if (onClick) {
    div.find('span').on('click', onClick);
  }
}

function toast_close() {
  undo = false;
  $('#toast').removeClass('show flash');
  toast_open = false;
  $('#toast').find('span').off('click', '*');
}

function btn_lock(btn) {
  lock = true;
  btn.prop('disabled', true);
  btn.addClass('loading');
}

function btn_unlock(btn) {
  lock = false;
  btn.prop('disabled', false);
  btn.removeClass('loading');
}

function load_market(filter) {
  btn_lock($('.sw-market .filters'));
  $.ajax({
    url: 'ajax/sw/market',
    data: filter,
    success: function(response) {
      if (response.data.players) {
        var template = Twig.twig({
          href: _FG_cfg.paths.views + '/ajax/market.twig?' + _FG_cfg.twig,
          load: function(template) {
            response.data.cfg = _FG_cfg;
            var output = template.render(response.data);
            $('.sw-content').html(output);
          }
        });
      }
    },
    complete: function() {
      $('#sw-market-filter select').prop('disabled', false);
      btn_unlock($('.sw-market .filters'));
    }
  });
}

Twig.extendFilter('pointsFormat', function (points, showPlusSign = false) {
  if (isNaN(points)) {
    return points;
  }
  var params = [
    _FG_cfg.hasDecimalPoints && Math.floor(points) != points ? 1 : 0,
    _FG_cfg.locale.decimal_point,
    _FG_cfg.locale.thousands_sep
  ];
  var ret = showPlusSign && points > 0 ? '+' : '';
  ret += Twig.filters.originalNumberFormat.call(
    this,
    points,
    params
  );
  return ret;
});

Twig.extendFilter('slug', function (str) {
  return getSlug(str);
});

Twig.extendFilter('shortName', function (str = '') {
  var short = str;
    str = str.split(' ');
    if (str.length > 1) {
      var initial = str[0].charAt(0);
      str.shift();
      str = str.join(' ');
      short = initial + '. ' + str;
    }
    return short;
});

Twig.extendFilter('splitName', function (str, returningPiece) {
  var firstName = '';
  var lastName = str;
  if (str.indexOf(' ') > -1) {
    var splittedName = str.split(' ');
    lastName = splittedName.pop();
    firstName = splittedName.join(' ');
  }
  if (returningPiece == 'firstName') {
    return firstName;
  } else {
    return lastName;
  }
});

Twig.extendFilter('string', function (number) {
  return String(number);
});

Twig.extendFunction("getGameAwayGoals", function(game) {
    return game.goals_away;
});

Twig.extendFunction("getGameAwayLogoUrl", function(game) {
    return game.awayLogoUrl;
});

Twig.extendFunction("getGameAwayTeamId", function(game) {
  return game.id_away;
});

Twig.extendFunction("getGameHomeGoals", function(game) {
    return game.goals_home;
});

Twig.extendFunction("getGameHomeLogoUrl", function(game) {
    return game.homeLogoUrl;
});

Twig.extendFunction("getGameHomeTeamId", function(game) {
    return game.id_home;
});

Twig.extendFunction("getGameStatus", function(game) {
    return game.status;
});

Twig.extendFunction("getGameTv", function(game) {
  return game.tv;
});

Twig.extendFunction("getGameDate", function(game) {
  return game.date.text;
});

Twig.extendFunction("getGameDateFormat", function(game) {
  return game.date.format;
});

Twig.extendFunction("getGameDateTimestamp", function(game) {
  return game.date.ts;
});

Twig.extendFunction("getGamePulse", function(game) {
  return game.pulse;
});
Twig.extendFunction("getGameTv", function(game) {
  return game.tv;
});

Twig.extendFunction("getAvatarColor", function(user) {
  return user.avatar.color;
});

Twig.extendFunction("getAvatarLetters", function(user) {
  return user.avatar.letters;
});

Twig.extendFunction("getAvatarImageUrl", function(user) {
  return user.avatar.imageUrl;
});

Twig.extendFunction("getPointsColor", function(points) {
  return getPointsColor(points);
});

Twig.extendFunction("getPointsBarSize", function(points) {
  if (isNaN(points) || points < 0) {
    return 0;
  }
  return points * 10;
});

function getPointsColor(points) {
  if (isNaN(points)) {
    return 'ns';
  }
  if (points < 0) {
    return 'critical';
  }
  if (points >= 0 && points < 2) {
    return 'failing';
  } else if (points >= 2 && points < 5) {
    return 'poor';
  } else if (points >= 5 && points < 7) {
    return 'fair';
  } else if (points >= 7 && points < 10) {
    return 'good';
  } else if (points >= 10 && points < 12) {
    return 'excellent';
  } else if (points >= 12) {
    return 'outstanding';
  }
}

function getCurrency(value) {
  var symbols = {
    'EUR': '€',
    'USD': '$',
  };
  var currency = _FG_cfg.currency.code;
  var symbol = symbols[currency];
  return (currency == 'EUR') ? value.toString().replace(/[^\d-]/, _FG_cfg.locale.decimal_point) + ' ' + symbol : symbol + value;
}

Twig.extendFunction('currency', function (value) {
  return getCurrency(value);
});

Twig.extendFunction('hasEmoji', function (value) {
  try {
    return new RegExp(/\p{Extended_Pictographic}/, 'u').test(value);
  } catch(e) {}
  return false;
});

Twig.extend(function() {
  if (!Twig.filters.originalNumberFormat) {
    Twig.filters.originalNumberFormat = Twig.filters.number_format;
  }

  Twig.extendFilter('number_format', function(value, params) {
    if (!params) {
      params = [0];
    }

    params[1] = params && params[1] !== undefined
      ? params[1] : _FG_cfg.locale.decimal_point;

    params[2] = params && params[2] !== undefined
      ? params[2] : _FG_cfg.locale.thousands_sep;

    return Twig.filters.originalNumberFormat.call(this, value, params);
  });
});

function trans(str, args) {
  return i18next.t(str, args);
}

function formatTimestampInSeconds(timestamp) {
  const date = new Date(timestamp * 1000);
  const day = date.getDate();
  const month = date.toLocaleString('es-ES', { month: 'long' }).toLowerCase();
  const translatedMonth = trans(month);
  const monthKey = translatedMonth.slice(0, 3);
  const formattedMonth = monthKey.charAt(0).toUpperCase() + monthKey.slice(1).toLowerCase();
  const year = date.getFullYear();

  return `${day} ${formattedMonth} ${year}`
}

Twig.extendFunction('trans', function (str, args) {
  return trans(str, args);
});

Twig.extendFunction('static_image', function (str) {
  return _FG_cfg.brand.staticImageBaseUrl + '/' + str;
});

Twig.extendFunction('format_timestamp_in_seconds', function (timestamp) {
  return formatTimestampInSeconds(timestamp);
});

function setCurrentPurchasePromo(promo)
{
  currentPromo = promo;
}

function getCurrentPurchasePromo()
{
  return currentPromo;
}

function setPromoForOrder(order, promo)
{
  localStorage.setItem(order, promo);
}

function getPromoForOrder(order)
{
  var promo = parseInt(localStorage.getItem(order), 10);
  if (isNaN(promo)) {
    return null;
  }

  return promo;
}

function removePromoForOrder(order)
{
  localStorage.removeItem(order);
}

function iOS_restoreTransaction(purchases, receipt) {
  if (!purchases) {
    console.log("restoreTransaction purchases arg is falsy");
    return;
  }

  if (purchases.constructor !== Array) {
    console.log("restoreTransaction purchases arg is not array");
    return;
  }

  if (purchases.length <= 0) {
    console.log("restoreTransaction purchases is empty");
    return;
  }

  if (!receipt) {
    console.log("restoreTransaction receipt arg is falsy");
    return;
  }

  var p = purchases[0];
  console.log(p);

  var purchase = {
    sku: p.product,
    order_id: p.transaction,
    token: receipt,
    platform: _FG_cfg.app
  };

  processRetryPurchase(purchase);
}

//This wrapper is needed due to limitations in the current android bridge which prevents us
//From using this method via setTimeout and any reactor callback.
//Anyhow, it is a good abstraction between android and ios
//Error: Java bridge method can't be invoked on a non-injected object
function listPendingPurchases() {
  if (typeof MRInterface !== "undefined" && typeof MRInterface.listPendingPurchases !== "undefined") {
    MRInterface.listPendingPurchases();
    return true;
  }
  if (_FG_cfg.app == 'ios' && typeof window.webkit.messageHandlers.listPendingPurchases !== 'undefined') {
    console.log("New list pending purchases");
    window.webkit.messageHandlers.listPendingPurchases.postMessage("");
    return true;
  }

  return false;
}

function listPendingPurchasesCallback(pendingPurchases) {

  if (!pendingPurchases) {
    console.log("listPendingPurchasesCallback with falsy argument");
    return;
  }

  var purchases = JSON.parse(pendingPurchases);
  if (!purchases.length || purchases.length <= 0) {
    console.log("No pending purchases found (two step)");
    return;
  }

  console.log(purchases);
  var p1 = JSON.parse(purchases[0]);
  var purchase = {
    sku: p1.productId,
    order_id: p1.orderId,
    token: p1.purchaseToken,
    platform: _FG_cfg.app
  };

  processRetryPurchase(purchase);
}

function getPurchaseByToken(token) {
  return new Promise(function (resolve, reject) {
    fetch("/api2/store/purchases?token=" + token, {
      method: "GET",
      credentials: "same-origin",
      headers: {
        "X-Auth": _FG_cfg.auth,
        "X_REQUESTED_WITH": "xmlhttprequest",
        "Content-Type": "application/json"
      }
    }).then(function(r) {
      if (r.status !== 200) {
        return reject(r.status);
      }
      var data = r.json();
      data.then(function (data) {
        resolve(data);
      });
    }).catch(function(e){
      reject(e);
    });
  });
}

function consumePurchase(purchase) {
  if (_FG_cfg.app == 'ios') {
    console.log("consuming order_id", purchase.order_id);
    window.webkit.messageHandlers.consumePurchase.postMessage(purchase.order_id);
  } else if (_FG_cfg.app == 'android') {
    MRInterface.consume(purchase.token);
  }
  sendPurchaseEvent(purchase.token);
}

function sendPendingPurchaseEvents()
{
  var pending = getPaypalPurchases();
  if (pending.length === 0) {
    console.log('No pending purchases');
    return;
  }

  var token = pending.pop();
  sendPurchaseEvent(token).then(function(){
    savePaypalPurchases(pending);
    sendPendingPurchaseEvents();
  }).catch(function(e){
    console.log("SendPurchaseEvent failed: ", e);
    setTimeout(sendPendingPurchaseEvents, 1000 * 30);
  });
}

function sendPurchaseEvent(token) {
  return new Promise(function (resolve, reject) {
    getPurchaseByToken(token).then(function (pinfo) {
      var now = new Date();
      var date = new Date(pinfo.date);

      console.log(now, date);
      var diff = (now.getTime() - date.getTime()) / 1000 / 60 / 60; //How many hours since purchase was created?
      if (diff > 1) {
        console.log('Purchase is too old');
        resolve();//More than an hour, will not send event
        return;
      }

      if (pinfo.status != 'completed') {
        reject('not completed');
        return;
      }

      var params = {
        'af_revenue': pinfo.gross,
        'af_currency': 'EUR', //All our gross are in euros
        'af_quantity': 1,
        'af_content': pinfo.sku,
      }
      sendEvent('af_purchase', params);
      resolve();
    }).catch(function (e) {
      if (e === 404) {
        resolve();
        return;
      }
      console.log("Couldn't get purchase by token so we can't send purchase event", e);
      reject(e)
    });
  });
}

function processRetryPurchase(purchase) {
  var promo = getPromoForOrder(purchase.order_id);
  if (promo) {
    purchase.id_promo = promo;
  }
  console.log("Process retry purchase: ", purchase);
  processPurchase(purchase).then(function (credits, cash) {
    if (credits) {
      updateCredits(credits);
      fullReload();
    }
    if (cash) {
      updateCash(cash);
      fullReload();
    }
    removePromoForOrder(purchase.order_id);
  }).catch(function () {
    console.log("processPurchase failed");
    setTimeout(listPendingPurchases, 5000);
  });
}

function processDirectPurchase(purchase) {
  var promo = getCurrentPurchasePromo();
  if (promo) {
    purchase.id_promo = promo;
    setPromoForOrder(purchase.order_id, promo);
  }
  console.log("Processing a direct purchase", purchase);

  processPurchase(purchase).then(function (items) {
    toast(trans('Compra realizada, ¡gracias!'));
    btn_unlock(btn_purchase);
    if (items.credits) {
      updateCredits(items.credits);
      fullReload();
    } else if (items.cash) {
      updateCash(items.cash);
      fullReload();
    } else {
      console.log("Performed purchase but no credits were returned");
    }
    removePromoForOrder(purchase.order_id);
  }).catch(function () {
    toast(trans("La compra se intentará de nuevo más tarde"), 'red');
    btn_unlock(btn_purchase);
    setTimeout(listPendingPurchases, 1000);
  });
}

//Returns a promise for processing a purchase
function processPurchase(purchase) {
  console.log("Processing purchase:", purchase);
  var appErrors = ["Existing Token", "discarted token", "Existing Order"];
  return new Promise(function (resolve, reject) {
    sendPurchase(purchase).then(function (response) {
      if (response.status >= 500 || response.status === 404) {
        console.log("SendPurchase failed with status: ", response.status);
        reject();
        return;
      }

      var data = response.json();
      data.then(function (data) {
        if (data.error && !appErrors.includes(data.error)) {
          console.log("error: ", data.error);
          reject();
          return;
        }
        consumePurchase(purchase);

        var items = {
          credits: data.credits ? data.credits : null,
          cash: data.cash ? data.cash : null
        };

        resolve(items);
      });
    }).catch(function (e) {
      reject(e);
    });
  });
}

//Called by MRInterface.launchTwoStepAppPurchase
function purchaseSuccessful(token, sku, order) {
  console.log("Two step purchase success: ", token, sku, order);
  var purchase = {
    sku: sku,
    order_id: order,
    token: token,
    platform: _FG_cfg.app
  };

  processDirectPurchase(purchase);
}

function startAndroidPurchase(sku, id_uc, ts) {
  if (typeof MRInterface.launchTwoStepAppPurchase !== 'undefined') {
    MRInterface.launchTwoStepAppPurchase(sku, id_uc, ts);
    console.log("Initializing android two step purchase", sku, id_uc, ts);
    return true;
  }

  console.log("Two step purchase api is missing");
  return false;
}

function startIOSPurchase(sku) {
  if (typeof window.webkit.messageHandlers.launchTwoStepAppPurchase !== 'undefined') {
    console.log("Starting new ios purchase");
    window.webkit.messageHandlers.launchTwoStepAppPurchase.postMessage('com.playmister.ios.' + sku);
    return true;
  }
  console.log("Two step purchase api is missing");
  return false;
}

function sendPurchase(purchase) {
  purchase.member_id = _FG_user.id_uc;
  return fetch("/api2/store/receiveboughtitems", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "X-Auth": _FG_cfg.auth,
      "X_REQUESTED_WITH": "xmlhttprequest",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(purchase)
  });
}

function savePaypalPurchase(token)
{
  var tokens = getPaypalPurchases();
  tokens.push(token);

  savePaypalPurchases(tokens);
}

function savePaypalPurchases(tokens)
{
  localStorage.setItem(paypalStoreKey, JSON.stringify(tokens));
}

/**
 * Marks a promo as dismissed. This will be used when pressing dismiss button
 * but it could be explicitly used in the promo button itself.
 */
function markPromo(e) {
  e.preventDefault();

  const cards = $(e.target).parents('.card-promo');

  if (cards.length !== 1) {
    return;
  }

  const id = $(cards[0]).attr('id');
  localStorage.setItem(id, false);
}

/**
 * Hides a promo. This will be called when pressing dismiss button in a promo.
 */
function hidePromo(e) {
  markPromo(e);

  const cards = $(e.target).parents('.card-promo');

  if (cards.length !== 1) {
    return;
  }

  $(cards[0]).remove();
}

/**
 * Hides already dismissed promos from the list of promos.
 */
function hidePromos() {
  const promos = $('.card-promo');

  for (let i = 0; i < promos.length; i++) {
    let id = $(promos[i]).attr('id');

    if (localStorage.getItem(id) === 'false') {
      $(promos[i]).remove();
    }
  }
}

function getPaypalPurchases()
{
  var tokens = localStorage.getItem(paypalStoreKey);
  if (typeof tokens !== 'string' || tokens.length === 0) {
    tokens = [];
  } else {
    tokens = JSON.parse(tokens);
  }

  return tokens;
}

function startExternalCheckout(sku, promo, community, iduc)
{
  var paymentData = {
    id_community: community,
    SKU: sku
  };

  var endpoint = "api2/store/buyWithExternalCheckout";
  paymentData.memberId = iduc;

  if (promo) {
    paymentData.id_promo = promo;
  }

  return new Promise(function(resolve, reject) {
    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-Auth": _FG_cfg.auth,
        "X_REQUESTED_WITH": "xmlhttprequest",
        "Content-Type": "application/json"
      },
      redirect: 'manual',
      body: JSON.stringify(paymentData)
    }).then(function(response){
      if (response.status >= 500 || response.status === 404) {
        console.log("buyWithExternalCheckout failed with status: ", response.status);
        reject();
        return;
      }
      response.json().then(function (data) {
        console.log(data);
        resolve(data);
        //savePaypalPurchase(data.token);
        document.location.href = data.externalUrl;
      }).catch(function(e) {
        console.log("Failed to parse response from startPayment", e);
        reject(e);
      });
    }).catch (function(e) {
      console.log("Error in startPayment request", e);
    });
  });
}

function startPayment(sku, promo, community, iduc)
{
  var paymentData = {
    Gateway: "paypal",
    id_community: community,
    SKU: sku
  };

  var endpoint = "api2/store/buyWithPaypal";
  paymentData.memberId = iduc;

  if (promo) {
    paymentData.id_promo = promo;
  }

  return new Promise(function(resolve, reject) {
    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-Auth": _FG_cfg.auth,
        "X_REQUESTED_WITH": "xmlhttprequest",
        "Content-Type": "application/json"
      },
      body: JSON.stringify(paymentData)
    }).then(function(response){
      if (response.status >= 500 || response.status === 404) {
        console.log("startPayment failed with status: ", response.status);
        reject();
        return;
      }
      response.json().then(function (data) {
        resolve(data);
        savePaypalPurchase(data.token);
      }).catch(function(e) {
        console.log("Failed to parse response from startPayment", e);
        reject(e);
      });
    }).catch (function(e) {
      console.log("Error in startPayment request", e);
    });
  });
}

function iOS_launchAppPurchase(sku, status, order, receipt) {
  if (status != 'ok') {
    btn_unlock(btn_purchase);
    return;
  }
  if (typeof window.webkit.messageHandlers.launchTwoStepAppPurchase !== "undefined") {
    if (typeof order !== "string" || order.length == 0) {
      order = "ios-blank-id";
    }
    var purchase = {
      sku: sku,
      order_id: order,
      token: receipt,
      platform: _FG_cfg.app
    };
    processDirectPurchase(purchase);
  } else {
    console.log("Two step purchase missing");
  }
}

function iOS_purchaseSuccessful() {
  iOS_launchAppPurchase.apply(null, arguments);
}

function retryPurchases() {
  if (!listPendingPurchases()) {
    console.log("Missing two step purchase api");
  }
}

function fetchCredits(cb) {
  $endpoint = "api2/users/currentcredits";
  $.ajax({
    url: $endpoint,
    type: 'GET',
    headers: { "Content-Type": "application/json", "X-User-ID": _FG_user.id },
    success: function (response) {
      console.log("Got:", response.credits);
      cb(response.credits, response.cash);
    },
    error: function () {
      console.log("Error fetching credits");
    },
    complete: function () {

    }
  });
}

function updateCredits(credits) {
  _FG_user.credits = credits;
  $('.credits-count').text(thousands(credits));
}

function updateCash(cash) {
  _FG_user.cash = cash;
  $('.cash-count').text(thousands(cash));
}

function updateMoneyUntilChange() {
  if (initialCredits == null) {
    initialCredits = _FG_user.credits;
  }

  if (initialCash == null) {
    initialCash = _FG_user.cash;
  }

  console.log("UpdateMoney until change", initialCredits, initialCash);

  fetchCredits(function (credits, cash) {
    if (initialCredits != credits) {
      console.log("New credits arrived", initialCredits, credits);
      initialCredits = thousands(credits);
      updateCredits(credits);
      fullReload();
    }

    if (initialCash != cash) {
      console.log("New cash arrived", initialCash, cash);
      initialCash = thousands(cash);
      updateCash(cash);
      fullReload();
    }

    console.log("Scheduling again");
    setTimeout(updateMoneyUntilChange, 2000);
  });
}

function ntf_subscribe(token) {
  if (_FG_cfg.isDebug) {
    return;
  }

  $.ajax({
    url: 'api/notifications/subscribe',
    data: { endpoint: token },
    success: function (response) {
      if (response.status == 'ok') {
        //console.log('Subscribed! Endpoint:', sub.endpoint);
      }
    }
  });
}

function ntf_unsubscribe() {
  reg.pushManager.subscribe({ userVisibleOnly: true }).
    then(function (pushSubscription) {
      sub = pushSubscription;
      sub.unsubscribe().then(function (event) {
        //console.log('Unsubscribed!', event);
      }).catch(function (error) {
        console.log('Error unsubscribing', error);
      });
    });
}

function attach_scroll(wrapper, element) {
  if (typeof attachedScrolls[element] !== 'undefined') return;
  attachedScrolls[element] = {
    hide_footer: false,
    last_scroll: 0
  };
  wrapper.scroll(function () {
    var scrollPosition = $(this).scrollTop();
    var wrapperScrollHeight = wrapper.get(0).scrollHeight || document.documentElement.scrollHeight;
    var scrollEnded = scrollPosition + wrapper.height() + 1 >= wrapperScrollHeight;
    if (scrollPosition > attachedScrolls[element].last_scroll && !scrollEnded) {
      if (!attachedScrolls[element].hide_footer) {
        $(element).addClass('footer-hide');
        attachedScrolls[element].hide_footer = true;
      }
    } else {
      if (attachedScrolls[element].hide_footer) {
        $(element).removeClass('footer-hide');
        attachedScrolls[element].hide_footer = false;
      }
    }
    attachedScrolls[element].last_scroll = scrollPosition;
  });
}

function getUserBalance(callback) {
  $.ajax({
    url: 'ajax/balance',
    success: function (response) {
      callback(response.data);
    },
    error: function() {}
  });
}

function getCookie(name) {
  let value = "; " + document.cookie;
  let parts = value.split("; " + name + "=");
  if (parts.length == 2) return parts.pop().split(";").shift();
}

function updateBalance(balanceData) {
  if (!balanceData) {
    getUserBalance(updateBalanceUI);
  } else {
    updateBalanceUI(balanceData);
  }
}

function getFormattedBalance(value, showMillions = false) {
  return Math.abs(value) > 999999 && showMillions ?
    (Math.round((value / 1000000) * 10) / 10).toFixed(1).replace('.', _FG_cfg.locale.decimal_point) + 'M' :
    thousands(value);
}

function updateBalanceUI(balanceData) {
  var headerBalance, footerBalance, footerFuture, footerMaxDebt;

  if (balanceData.current != _FG_user.balance.current) {
    $('.btn-balance').addClass('flash');
    setTimeout(function() {
      $('.btn-balance').removeClass('flash');
    }, 400);
  }

  _FG_user.balance = balanceData;

  headerBalance = getFormattedBalance(balanceData.current, true);
  footerBalance = getFormattedBalance(balanceData.current);
  footerFuture = getFormattedBalance(balanceData.future);
  footerMaxDebt = getFormattedBalance(balanceData.maxDebt);

  $('.balance-real-current').text(footerBalance);
  $('.header-top .balance-real-current').text(headerBalance);
  $('.balance-real-future').text(footerFuture);
  $('.balance-real-maxdebt').text(footerMaxDebt);

  if (balanceData.current >= 0) {
    $('.btn-balance').removeClass('negative');
    $('.balance-real-current').removeClass('pulse red');
  } else {
    $('.btn-balance').addClass('negative');
    $('.balance-real-current').addClass('pulse red');
  }

  if (balanceData.future >= 0) {
    $('.balance-real-future').removeClass('pulse red');
  } else {
    $('.balance-real-future').addClass('pulse');
  }

  if (balanceData.maxDebt > 0) {
    $('.balance-real-maxdebt').removeClass('pulse');
  } else {
    $('.balance-real-maxdebt').addClass('pulse');
  }
}

function updateFutureBalance(value) {
  var futureBalance = $('.live-balance-future');
  var future = _FG_user.balance.future - value;
  futureBalance.text(getFormattedBalance(future));
  if (future >= 0) {
    futureBalance.removeClass('pulse red');
  } else if (_FG_user.balance.future >= 0 && future < 0) {
    futureBalance.addClass('pulse');
  }
}

function showPopupBalance() {
  if (typeof _FG_user.balance.future === 'undefined') {
    getUserBalance(showPopupBalanceUI);
  } else {
    showPopupBalanceUI(_FG_user.balance);
  }
}

function showPopupBalanceUI(balanceData) {
  _FG_user.balance = balanceData;
  $('.live-balance-current').text(getFormattedBalance(_FG_user.balance.current));
  if (_FG_user.balance.current < 0) $('.live-balance-current').addClass('red');
  $('.live-balance-future').text(getFormattedBalance(_FG_user.balance.future));
  if (_FG_user.balance.future < 0) $('.live-balance-future').addClass('red');
  $('.live-balance-maxdebt').text(getFormattedBalance(_FG_user.balance.maxDebt));
  $('#input-range').attr('max', _FG_user.balance.maxDebt ?? 999999999);
  if (_FG_user.balance.maxDebt === null) {
    $('.live-balance-maxdebt').hide();
  }
  $('.live-balance-top').addClass('show');
  $('html').addClass('live-balance-top-show');
}

function open_bid(type = 'bid') {
  showPopupBalance();
  if (_FG_user.balance.maxDebt) {
    if (
      (type == 'bid' && popupData.market.price > _FG_user.balance.maxDebt && !popupData.bid.isActive) ||
      (type == 'clause' && popupData.clause.value > _FG_user.balance.maxDebt)
    ) {
      $('#btn-send').attr('disabled', 'disabled');
      $('.live-balance-maxdebt').removeClass('pulse');
      setTimeout(function() {
        $('.live-balance-maxdebt').addClass('pulse');
      }, 1);
    }
  } else if (popupData.bid.isActive) {
    var maxDebt = _FG_user.balance.maxDebt === null
      ? 999999999
      : Math.floor(_FG_user.balance.maxDebt + parseInt(popupData.bid.input));
    $('.live-balance-maxdebt').text(getFormattedBalance(maxDebt));
    $('#input-range').attr('max', maxDebt);
    $('#input-range').attr('value', popupData.bid.input);
  }
  updateFutureBalance((popupData.bid.isActive) ? 0 : parseInt($('.input-amount').val().replace(/[^\d-]/g, '')));
}

function open_sign() {
  open_bid();
}

function open_loan_request() {
  open_bid();
}

function open_clause_pay() {
  open_bid('clause');
}

function open_clause_set() {
  showPopupBalance();
}

function updateBidBtnColor(btn, action) {
  btn.each(function() {
    if (action.indexOf('remove') > -1) {
      $(this).removeClass('btn--accent').addClass('btn--' + $(this).data('style'));
    } else {
      $(this).removeClass('btn--' + $(this).data('style')).addClass('btn--accent');
    }
  });
}

function updateBidText(btn, response) {
  if (response.action.indexOf('remove') > -1) {
    btn.text(btn.data('text'));
  } else if (['bid', 'update', 'loan'].includes(response.action)) {
    var html;
    if (typeof response.barter !== 'undefined' && Object.keys(response.barter).length > 0) {
      html = (response.bid / 1000000).toFixed(1).replace('.', _FG_cfg.locale.decimal_point) +
          ' M <div class="player-avatar player-avatar--xs" data-players="' + Object.keys(response.barter).length + '">' +
          '<img src="' +  response.barter[Object.keys(response.barter)[0]].photoUrl + '" loading="lazy"></div>';
    } else {
      html = thousands(response.bid);
    }
    btn.html(html);
  } else if (['add'].includes(response.action)) {
    btn.text(trans('Cedible'));
  }
}

function updateSaleText(btn, response) {
  if (['sale', 'update'].includes(response.action)) {
    btn.text(trans('En venta'));
  } else if (response.action.indexOf('remove') > -1) {
    btn.text(btn.data('text'));
  }
}

function updateReceivedOffersCount(offers) {
  var newText = trans('MARKET_OFFER_RECEIVED_N', { n: offers.pending + offers.accept + offers.decline });
  $('.market-options .offers-received .text').text(newText);
  if (swOpened) {
    $('.sw-title').text(newText);
  }
  if (offers.pending < 1) {
    $('.pending-offers-badge').hide();
    $('li#c-' + _FG_user.id_community + ' .offers-badge').hide();
    $('.market-options .offers-received').removeClass('btn--accent');
  } else {
    $('.pending-offers-badge').show().text(offers.pending);
    $('li#c-' + _FG_user.id_community + ' .offers-badge').show();
    $('.market-options .offers-received').addClass('btn--accent');
  }
  if (offers.pending < 1 && offers.accept < 1 && offers.decline < 1) {
    $('.market-options .offers-received').remove();
    if (swOpened) {
      $('.sw-market-offers + .empty').show();
    }
  }
}

function getBidBtn(response) {
  return $(`.btn[data-id_owner="${response.owner}"][data-id_player="${response.id_player}"]:not(.btn-clause)`);
}

function clearBidHeader(btn) {
  btn.parents('li').find('.header .right').empty();
}

function callback_sale(response) {
  var btn = getBidBtn(response);
  updateBidBtnColor(btn, response.action);
  updateSaleText(btn, response);
  updateReceivedOffersCount(response.offers);
  clearBidHeader(btn);
  if (['sale', 'update'].includes(response.action)) {
    btn.parents('li').find('.header .right').text(thousands(response.price));
  } else if (window.location.href.indexOf('/market') > -1 && response.owner == _FG_user.id_uc) {
    btn.parents('li').remove();
  }
}

function callback_resale(response) {
  var btn = getBidBtn(response);
  btn.data('ends', response.ends);
  btn.parents('.sw-market-offers')
    .find(`.btn[data-id_owner="${response.owner}"][data-id_player="${response.id_player}"]`)
    .parents('li')
    .remove();
  updateReceivedOffersCount(response.offers);
  toast(trans('Acción realizada'), 'green');
}

function callback_loanable(response) {
  var btn = getBidBtn(response);
  updateBidBtnColor(btn, response.action);
  updateBidText(btn, response);
  if (response.action.indexOf('remove') > -1 && window.location.href.indexOf('/market') > -1) {
    btn.parents('li').remove();
  }
}

function callback_bid(response) {
  var btn = getBidBtn(response);
  updateBidBtnColor(btn, response.action);
  updateBidText(btn, response);
  updateBalance(response.balance);
  clearBidHeader(btn);
}

function callback_loan_request(response) {
  var btn = getBidBtn(response);
  updateBidBtnColor(btn, response.action);
  updateBidText(btn, response);
  updateBalance(response.balance);
  clearBidHeader(btn);
  if (['loan', 'update'].includes(response.action)) {
    btn.parents('li').find('.header .right').text(trans('DAYS_N', { daysCount: response.days }));
  }
}

function callback_sign(response) {
  var btn = getBidBtn(response);
  btn.prop('disabled', true);
  toast(trans('Fichaje realizado'));
  updateBalance(response.balance);
}

function callback_settings(response) {
  toast(trans('Configuración guardada'), 'green');
  if (response.key == 'nav_bottom') {
    $('html').toggleClass('nav-bottom nav-top');
  }
  if (response.key == 'notifications_gameweek_match_points') {
    var strings = {
      0: trans('Ninguno'),
      1: trans('Todos'),
      2: trans('En que jugadores míos han disputado'),
      3: trans('En que jugadores de algún miembro de la liga han disputado')
    };
    var li = $('#notifications_gameweek_match_points');
    li.data('value', response.value);
    li.find('p').text(strings[response.value]);
  }
}

function callback_clause_pay(response) {
  return 'feed';
}

function callback_loan_confirm(response) {
  return 'feed';
}

function callback_contest_join(response) {
  return 'feed';
}

function formatIntlDate(format, ts) {
  var days = [trans('Domingo'), trans('Lunes'), trans('Martes'), trans('Miércoles'), trans('Jueves'), trans('Viernes'), trans('Sábado')];
  var months = [trans('enero'), trans('febrero'), trans('marzo'), trans('abril'), trans('mayo'), trans('junio'), trans('julio'), trans('agosto'), trans('septiembre'), trans('octubre'), trans('noviembre'), trans('diciembre')];
  var date = new Date(ts * 1000);
  var time = date.getHours() + ':' + (date.getMinutes() < 10 ? '0' + date.getMinutes() : date.getMinutes());
  return format
    .replace('H:mm', time)
    .replace('EEE', days[date.getDay()].substr(0, 3))
    .replace(/^d/, date.getDate())
    .replace('MMM', months[date.getMonth()].substr(0, 3))
    .replace(/'/g, '');
}

function local_time() {
  $('.tz').each(function () {
    var e = $(this);
    var format = e.data('format');
    var ts = e.data('ts');
    e.text(formatIntlDate(format, ts));
    e.removeClass('tz');
  });
}

function facebookLogoutCallback(success) {
  toast(trans(success ? 'Éxito' : 'Error'));
}

function input_error(e, msg) {
  if (!e.classList.contains('error')) {
    $(e).addClass('error').parent().append('<div class="error">' + msg + '</div>').width();
    $(e).parent().find('div.error').css('opacity', 1);
    $('#btn-send').attr('disabled', true);
  } else {
    $(e).parent().find('div.error').text(msg);
  }
}

function input_ok(e) {
  if (e.classList.contains('error')) {
    $(e).removeClass('error').parent().find('div.error').css('opacity', 0);
    setTimeout(function () {
      $(e).parent().find('div.error').remove();
    }, 110);
    $('#btn-send').attr('disabled', false);
  }
}

function check_name(e) {
  var test = e.value.length > 0;
  if (test) {
    input_ok(e);
    $(e).removeClass('check-on-input');
  } else {
    input_error(e, trans('El nombre no puede estar vacío'));
    $(e).addClass('check-on-input');
  }
  return test;
}

function check_email(e) {
  var regex = /^(([^<>()\[\]\\.,;:\s@"]+(\.[^<>()\[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/;
  var test = regex.test(String(e.value).toLowerCase());
  if (test) {
    input_ok(e);
    $(e).removeClass('check-on-input');
  } else {
    input_error(e, trans('El email no es correcto'));
    $(e).addClass('check-on-input');
  }
  return test;
}

function check_password(e) {
  var test = e.value.length >= 6;
  if (test) {
    input_ok(e);
    $(e).removeClass('check-on-input');
  } else {
    input_error(e, trans('PASSWORD_MUST_HAVE_N_CHARS', { count: 6 }));
    $(e).addClass('check-on-input');
  }
  return test;
}

function check_code(e) {
  var test = e.value.length > 0;
  if (test) {
    input_ok(e);
    $(e).removeClass('check-on-input');
  } else {
    input_error(e, trans('El código no puede estar vacío'));
    $(e).addClass('check-on-input');
  }
  return test;
}

function check_debt(value) {
  var error = false;
  if (_FG_user.balance.maxDebt !== null && ((!popupData.bid.isActive && value > _FG_user.balance.maxDebt) || (popupData.bid.isActive && value > _FG_user.balance.maxDebt + parseInt(popupData.bid.input)))) {
    error = true;
    $('.balance-real-maxdebt').addClass('pulse');
  } else {
    $('.balance-real-maxdebt').removeClass('pulse');
  }
  return error;
}

function check_bid(e) {
  var value = parseInt(e.value.replace(/[^\d-]/g, ''));
  var marketValue = popupData.value;

  if (check_debt(value)) {
    input_error(e, trans('No puedes superar tu deuda máxima'));
  } else if (value < marketValue && !popupData.hasBarter) {
    input_error(e, trans('No puedes pujar por debajo del valor de mercado'));
  } else if (value < 0 || isNaN(value)) {
    input_error(e, trans('La puja debe ser mayor que 0'));
  } else if (value > 1000000000) {
    input_error(e, trans('No puedes pujar por encima de los 1.000M'));
  } else {
    input_ok(e);
  }
  updateFutureBalance((popupData.bid.isActive) ? value - popupData.bid.input : value);
}

function check_sale(e) {
  var value = parseInt(e.value.replace(/[^\d-]/g, ''));
  if (value < 1 || isNaN(value)) {
    input_error(e, trans('El precio debe ser mayor que 0'));
  } else {
    input_ok(e);
  }
}

function check_loan(e) {
  var value = parseInt(e.value.replace(/[^\d-]/g, ''));
  if (check_debt(value)) {
    input_error(e, trans('No puedes superar tu deuda máxima'));
  } else if (value < 1 || isNaN(value)) {
    input_error(e, trans('La oferta debe ser mayor que 0'));
  } else if (_FG_user.loans_floor > 0 && value < (popupData.value * _FG_user.loans_floor / 100) * $('input[type="hidden"][name="days"]').val()) {
    input_error(e, trans('LOAN_VALUE_ERROR_COST_PER_DAY_UNDER_PLAYER_VALUE', { percent: _FG_user.loans_floor }));
  } else if (value > 1000000000) {
    input_error(e, trans('No puedes pujar por encima de los 1.000M'));
  } else {
    input_ok(e);
  }
  updateFutureBalance((popupData.bid.isActive) ? value - popupData.bid.input : value);
}

function update_popup_send() {
  if ($('input[name=action]').val() == 'remove') {
    $('#btn-send').text(trans('Actualizar')).removeClass('btn--red').addClass('btn--primary');
    $('input[name=action]').val('update');
  }
}

function callback_sw_gameweek(post) {
  scrollToSelectedGameweek($('.gameweek-wrapper .gameweek-selector-inline'));
  refreshAds();
  dispatchLoadPartialEvent();
}

function loadSelectedGameweek(gameweekId) {
  post = {
    post: 'gameweek',
    id: gameweekId
  };
  sw_open('gameweek', post);
}

function callback_sw_store(post) {
  // Force the onclick of the first element to update the id_promo of the #purchase-button
  $(".sw-store .packs .box").eq(0).click();
}

Twig.extendFilter('keys_int', function(arr) {
  return Object.keys(arr).map(Number);
});

function updateBarterInputs() {
  var barter = $('.btn-barter').data('barter');
  $('#form-bid input[name=barter]').remove();
  $('.btn-barter-player').remove();
  $('.barter-players').empty();
  for (var id in barter) {
    var html = `<button type="button" class="btn btn--wide btn-barter-player" data-id_player="${barter[id].id_player}">
                  <div class="player-avatar player-avatar--xs">
                    <img src="${barter[id].photoUrl || barter[id].photourl}" loading="lazy">
                  </div>
                  <div class="name">${barter[id].name}</div>
                  <div class="value">· € ${thousands(barter[id].value)}</div>
                  ${getSVG('cross-circle')}
                </button>`;
    $('.barter-players').append(html);
  }
  $('#form-bid').append('<input type="hidden" name="barter" value="' + Object.keys(barter).join(',') + '">');
  if (Object.keys(barter).length > 2) {
    $('.btn-barter').prop('disabled', true);
  } else {
    $('.btn-barter').prop('disabled', false);
  }
  update_popup_send();
  popupData.hasBarter = Object.keys(barter).length > 0;
}

function addBarterPlayer(btn) {
  var barter = $('.btn-barter').data('barter');
  barter[btn.id_player] = btn;
  $('.btn-barter').data('barter', barter);
}

function removeBarterPlayer(btn) {
  var barter = $('.btn-barter').data('barter');
  delete barter[btn.id_player];
  $('.btn-barter').data('barter', barter);
}

function showStoreFloatingBtn() {
  $('#btn-store-floating').addClass('show');
}

function hideStoreFloatingBtn() {
  $('#btn-store-floating').removeClass('show');
}

function updateLastSeen() {
  let dateCookie = getCookie("fg_last_seen");
  if (dateCookie) {
    console.log("UPDATE SEEN Not updating last seen cookie");
    return;
  }

  $.ajax({
    url: 'ajax/update-last_seen',
    success: function (response) {
    },
    error: function() {}
  });
}

function giphySearch(text, input = false) {
  limit = input ? 10 : 10;
  text = encodeURI(text);
  $('.giphy-results').append('<div class="loading"></div>');
  delete $.ajaxSettings.headers['X-Auth'];
  $.ajax({
    url: 'https://api.giphy.com/v1/gifs/search?q=' + text + '&api_key=HmL1Rhx5T8GQj1FTPXuRspqYlnVNYApj&limit=' + limit + '&offset=' + giphyOffset + '&rating=G',
    type: 'GET',
    success: function (response) {
      if (input) {
        $('.giphy-results').empty();
      }
      response.data.forEach(function(gif) {
        $('.giphy-results').append('<div class="img" data-id="' + gif.id + '"><img src="https://i.giphy.com/media/' + gif.id + '/100h.gif" loading="lazy"></div>');
      });
    },
    complete: function() {
      giphyLoading = false;
      $('.giphy-results .loading').remove();
    }
  });
  $.ajaxSettings.headers['X-Auth'] = _FG_cfg.auth;
}

function shuffle(a) {
    var j, x, i;
    for (i = a.length - 1; i > 0; i--) {
        j = Math.floor(Math.random() * (i + 1));
        x = a[i];
        a[i] = a[j];
        a[j] = x;
    }
    return a;
}

function customChartTooltip(ctx) {
  var element = document.getElementById('chartjs-tooltip');
  if (!element) {
    element = document.createElement('div');
    element.id = 'chartjs-tooltip';
    ctx.chart.canvas.parentNode.appendChild(element);
  }
  if (ctx.tooltip.opacity === 0) {
    element.style.opacity = 0;
    return;
  }
  element.innerHTML = '<div class="label">' + ctx.tooltip.dataPoints[0].label + '</div>';
  element.innerHTML += '<div class="value">' + ctx.tooltip.dataPoints[0].formattedValue + '</div>';
  var positionY = ctx.chart.canvas.offsetTop;
  var positionX = ctx.chart.canvas.offsetLeft;
  element.style.opacity = 1;
  element.style.left = positionX + ctx.tooltip.caretX + 'px';
  element.style.top = positionY + ctx.tooltip.caretY + 'px';
}

function getCSSVar(name, element = document.documentElement) {
  const value = getComputedStyle(element).getPropertyValue(name);
  if (value) return value.trim();
  if (element.parentElement) return getCSSVar(name, element.parentElement);
  return null;
}

function valuesChart(values) {
  var accentColor = getCSSVar('--accentColor');
  var transparentColor = getCSSVar('--accentTransparent');
  var labels = [];
  var dataset = [];
  values.points.forEach(function(point) {
    labels.push(point.date);
  });
  values.points.forEach(function(point) {
    dataset.push(point.value);
  });
  var ctx = document.getElementById("canvas").getContext("2d");
  var gradientStroke = ctx.createLinearGradient(0, 0, ctx.canvas.clientWidth, 0);
  gradientStroke.addColorStop(0, transparentColor);
  gradientStroke.addColorStop(1, accentColor);
  var lineChartData = {
    labels: labels,
    datasets: [{
      cubicInterpolationMode: 'monotone',
      tension: 0.5,
      fill: false,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointHoverBackgroundColor: accentColor,
      borderColor: gradientStroke,
      borderWidth: 2,
      data: dataset
    }]
  };
  var chartArgs = {
    type: 'line',
    data: lineChartData,
    options: {
      plugins: {
        legend: {
          display: false
        },
        tooltip: {
          enabled: false,
          external: customChartTooltip,
          mode: 'index',
          axis: 'x',
          intersect: false
        },
      },
      scales: {
        y: {
          min: values.min.value,
          max: values.max.value,
          border: {
            display: false,
            dash: [2,2]
          },
          grid: {
            display: true,
            tickLength: 0,
            color: getCSSVar('--bg-secondary')
          },
          ticks: {
            count: 3,
            mirror: true,
            padding: 0,
            color: getCSSVar('--fg-secondary'),
            callback: function(value) {
              if (isNaN(value)) {
                return;
              }
              return Number(value/1000000).toFixed(1).replace('.', _FG_cfg.locale.decimal_point) + 'M';
            },
          },
        },
        x: {
          display: false
        }
      },
      hover: {
        mode: 'index',
        axis: 'x',
        intersect: false
      },
      animation: false,
      maintainAspectRatio: false
    }
  };
  var chart = new Chart(ctx, chartArgs);
}

function tutorialLoadStep(stepNumber) {
  var step = tutorial.steps[stepNumber];
  var eventName;
  if (eventsPerStep[stepNumber]) {
    eventName = step.tutorial + eventsPerStep[stepNumber];
    sendEvent(eventName);
  }
  eventName = step.tutorial + '_tutorial_step';
  sendEvent(eventName, {step: stepNumber});
  if (step.type == 'popup') {
    if (tutorialTooltip) {
      tutorialHideTooltip();
    }
    tutorialShowPopup(step.view);
  } else {
    tutorialHidePopup();
    if (step.type == 'tooltip') {
      tutorialShowTooltip(step);
    }
  }
  if (typeof step.callback === 'function') {
    step.callback();
  }
}

function tutorialShowPopup(view) {
  $('.tutorial-popup-content').css('max-height', $(window).height() * 0.9 + 'px');
  var post = {
    cfg: _FG_cfg,
    tutorial: tutorial
  };
  template = Twig.twig({
    href: _FG_cfg.paths.views + '/tutorial/' + view + '.twig?' + _FG_cfg.twig,
    async: true,
    load: function (template) {
      output = template.render(post);
      if (!tutorialPopup) {
        $('.tutorial-popup-content').html(output);
        $('html').css('overflow', 'hidden');
        $('.tutorial-overlay').css('display', 'flex').width();
        $('.tutorial-overlay').addClass('show');
      } else {
        $('.tutorial-popup-content').css('opacity', 0);
        setTimeout(function () {
          $('.tutorial-popup-content').html(output);
          $('.tutorial-popup-content').css('opacity', 1);
        }, 100);
      }
      $('html').css('pointer-events', 'auto');
      tutorialPopup = true;
    }
  });
}

function tutorialHidePopup() {
  $('html').css('overflow', '').css('pointer-events', 'auto');
  $('.tutorial-overlay').removeClass('show');
  tutorialPopup = false;
  setTimeout(function () {
    $('.tutorial-overlay').hide();
    $('.tutorial-popup-content').empty();
  }, 110);
}

function tutorialShowTooltip(step) {
  var tooltipElement;
  var tooltipHTML = '<div class="tutorial-tooltip"><div class="tutorial-tooltip-bubble"><p>' + step.text + '</p>';
  if (step.button) {
    if (step.end) {
      tooltipHTML += '<button class="btn btn--primary btn--md btn-tutorial-close" data-tutorial="' + step.tutorial + '">' + step.button.text + '</button>';
    } else {
      tooltipHTML += '<button class="btn btn--primary btn--md btn-tutorial-next" data-next="' + step.button.next + '">' + step.button.text + '</button>';
    }
  }
  tooltipHTML += '</div><div class="tutorial-tooltip-arrow" data-popper-arrow></div></div>';
  if (!tutorialTooltip) {
    $('body').prepend(tooltipHTML);
    tooltipElement = $('.tutorial-tooltip');
    $('html').css('overflow', 'hidden').css('pointer-events', 'none');
    if (step.pointer == 'tooltip') {
      tooltipElement.find('.btn').css('pointer-events', 'auto');
    } else if (step.pointer == 'target') {
      step.target.css('pointer-events', 'auto');
    }
    Popper.createPopper(step.target.first().get(0), tooltipElement.get(0), {
      modifiers: [{
        name: 'offset',
        options: {
          offset: [0, 20],
        },
      }],
    });
    tutorialTooltipExtras(step);
    setTimeout(function () {
      tooltipElement.addClass('show');
      $('.tutorial-tap').addClass('show');
      $('.tutorial-outline').addClass('show');
    }, 100);
  } else {
    tooltipElement = $('.tutorial-tooltip');
    tooltipElement.removeClass('show');
    $('.tutorial-tap').removeClass('show');
    $('.tutorial-outline').removeClass('show');
    setTimeout(function () {
      tooltipElement.remove();
      $('.tutorial-tap').remove();
      $('.tutorial-outline').remove();
      $('body').prepend(tooltipHTML);
      tooltipElement = $('.tutorial-tooltip');
      if (step.pointer == 'tooltip') {
        tooltipElement.find('.btn').css('pointer-events', 'auto');
      } else if (step.pointer == 'target') {
        step.target.css('pointer-events', 'auto');
      }
      Popper.createPopper(step.target.first().get(0), tooltipElement.get(0), {
        modifiers: [{
          name: 'offset',
          options: {
            offset: [0, 20],
          },
        }],
      });
      tutorialTooltipExtras(step);
      tooltipElement.addClass('show');
      $('.tutorial-tap').addClass('show');
      $('.tutorial-outline').addClass('show');
    }, 210);
  }
  if (step.next) {
    $('body').on('click', step.trigger, function() {
      tutorialLoadStep(step.next);
    });
  } else if (step.end) {
    $('body').on('click', step.trigger, function() {
      tutorialClose(step.tutorial);

      if (step.onEndRefresh) {
        fullReload();
      }
    });
  }
  tutorialTooltip = true;
}

function tutorialHideTooltip() {
  $('html').css('overflow', '').css('pointer-events', 'auto');
  $('.tutorial-tooltip').removeClass('show');
  $('.tutorial-tap').removeClass('show');
  $('.tutorial-outline').removeClass('show');
  tutorialTooltip = false;
  setTimeout(function () {
    $('.tutorial-tooltip').remove();
    $('.tutorial-tap').remove();
    $('.tutorial-outline').remove();
  }, 110);
}

function tutorialTooltipExtras(step) {
  var hintWidth, hintHeight, hintTop, hintLeft;
  if (step.hint == 'tap') {
    hintWidth = step.target.first().outerWidth();
    hintHeight = step.target.first().outerHeight();
    hintTop = step.target.first().offset().top;
    hintLeft = step.target.first().offset().left;
    var tap = '<div class="tutorial-tap"></div>';
    $('body').prepend(tap);
    $('.tutorial-tap')
      .css('top', (hintTop + ((hintHeight - 50) / 2)) + 'px')
      .css('left', (hintLeft + ((hintWidth - 50) / 2)) + 'px');
  } else if (step.hint == 'outline') {
    hintWidth = step.target.first().outerWidth() + 20;
    hintHeight = step.target.length > 1 ? (step.target.last().offset().top - step.target.first().offset().top) : step.target.first().outerHeight() + 20;
    hintTop = step.target.first().offset().top - 10;
    hintLeft = step.target.first().offset().left - 10;
    var outlineHTML = '<div class="tutorial-outline"></div>';
    $('body').prepend(outlineHTML);
    $('.tutorial-outline')
      .css('width', hintWidth + 'px')
      .css('height', hintHeight + 'px')
      .css('top', hintTop + 'px')
      .css('left', hintLeft + 'px');
  }
}

function tutorialClose(tutorial) {
  var cookieName = tutorial + '_tutorial_seen';
  Cookies.set(cookieName, 1, { expires: 365 * 10, path: '/' });
  tutorialHideTooltip();
  tutorialHidePopup();

  if (sessionStorage.getItem('coupon') === null) {
    return;
  }

  sws.store.post.coupon = sessionStorage.getItem('coupon');
  sessionStorage.removeItem('coupon');
  window.location.hash = "#store";
}

function svgChangeIcon(element, icon) {
  var svgUse = element.find('svg use');
  if (svgUse.length > 0) {
    var oldHref = svgUse.attr('href');
    var newHref = oldHref.substring(0, oldHref.indexOf('#'));
    svgUse.attr('href', newHref + '#' + icon);
  } else {
    element.append(getSVG(icon));
  }
}

function thousands(num) {
  num = num + '';
  var clean = num.replace(/[^\d-]/g, '');
  return clean.replace(/\B(?=(\d{3})+(?!\d))/g, _FG_cfg.locale.thousands_sep);
}

function reset_press() {
  pressed = false;
  speed = 201;
}

function input_value(action, step, increment) {
  var input = $('.input-sub-add input');
  var inputRange = $('#input-range');
  var value = parseInt(input.val().replace(/[^\d-]/g, ''));
  var num = (action == 'add') ? value + increment : value - increment;
  const newValueStr = thousands(num);
  input.attr('value', newValueStr);
  input.val(newValueStr);
  input.trigger('input', ['btn']);
  if (inputRange.length > 0) {
    inputRange.attr('value', num);
    inputRange.val(num);
    update_range_bg($('#input-range').get(0));
  }
}

function update_range_bg(e) {
  var percent = ((e.value - e.min) / (e.max - e.min)) * 100;
  $(e).css('background', 'linear-gradient(to right, ' + getCSSVar('--accentColor') + ' 0%, ' + getCSSVar('--accentColor') + ' ' + percent + '%, ' + getCSSVar('--bg-tertiary') + ' ' + percent + '%, ' + getCSSVar('--bg-tertiary') + ' 100%)');
}

function isTouchOutsideElement(element, event) {
  var coordinates = {
    left: element.offset().left,
    top: element.offset().top,
    right: element.offset().left + element.width(),
    bottom: element.offset().top + element.height()
  };
  if (event.originalEvent.changedTouches[0].pageX < coordinates.left ||
    event.originalEvent.changedTouches[0].pageX > coordinates.right ||
    event.originalEvent.changedTouches[0].pageY < coordinates.top ||
    event.originalEvent.changedTouches[0].pageY > coordinates.bottom) {
    return true;
  }
  return false;
}

function writeRatingPromptBubble() {
  ratingPrompt.find('.app-rating-text').text('');
  ratingPrompt.find('.app-rating-buttons').removeClass('show');
  setTimeout(function() {
    ratingPrompt.find('.app-rating-buttons').hide();
  }, 100);
  ratingPromptTextArray = ratingPromptText.split(/.*?/u);
  setTimeout(function() {
    ratingPromptInterval = setInterval(function() {
      if (ratingPromptTextArray.length > 0) {
        ratingPrompt.find('.app-rating-text').append(ratingPromptTextArray.shift());
      } else {
        clearInterval(ratingPromptInterval);
        ratingPrompt.find(ratingPromptButtons).css('display', 'flex');
        setTimeout(function() {
          ratingPrompt.find(ratingPromptButtons).addClass('show');
        }, 10);
      }
    }, 30);
  }, 500);
}

function saveRatingClick() {
  var post = {
    key: 'app_rating_clicked',
    value: 1
  };
  $.ajax({
    url: 'ajax/settings',
    data: post
  });
}

function parseEventSeenThreshold(element) {
  var threshold = parseFloat(element.getAttribute('data-event-seen-threshold'));

  if (isNaN(threshold)) {
    return 0.9;
  }

  if (threshold > 1) {
    threshold = threshold / 100;
  }

  return Math.max(0, Math.min(1, threshold));
}

function observeEventSeenAnchor(element) {
  if (element.dataset.seen === 'true') {
    return;
  }

  var eventAnchor = $(element);
  var eventName = eventAnchor.data('event-seen');
  var eventParams = eventAnchor.data('params') || {};
  var eventOptions = typeof getAnalyticsEventOptions === 'function'
    ? getAnalyticsEventOptions(element, true)
    : {
      amplitude: element.hasAttribute('data-event-amplitude'),
      once: element.hasAttribute('data-event-seen-once')
    };
  var threshold = parseEventSeenThreshold(element);

  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (entry.intersectionRatio < threshold) {
        return;
      }

      entry.target.dataset.seen = true;
      eventAnchor.data('seen', true);
      observer.disconnect();
      sendEvent(eventName, eventParams, eventOptions);
    });
  }, { threshold: threshold });

  observer.observe(element);
  eventSeenObservers.push(observer);
}

function disconnectEventSeenObservers() {
  eventSeenObservers.forEach(function(observer) {
    observer.disconnect();
  });
  eventSeenObservers = [];
}

Twig.extendFunction('teamLogo', function (logoUrl, size, imgClass, isActive) {
  return getTeamLogo(logoUrl, size, imgClass, isActive);
});

function getTeamLogo(logoUrl, size = 20, imgClass = 'team-logo', isActive = true) {
  if (!isActive) {
    imgClass += ' team-logo--disabled';
  }

  return '<img class="' + imgClass + '" width="' + size + '" height="' + size + '" src="' + logoUrl + '" loading="lazy">';
}

Twig.extendFunction('playerPosition', function (position, divClass) {
  return getPlayerPosition(position, divClass);
});

function getPlayerPosition(position, divClass = '') {
  return '<div class="player-position ' + divClass + '" data-position="' + position + '"></div>';
}

Twig.extendFunction('svg', function (icon, size, className) {
  return getSVG(icon, size, className);
});

function getSVG(icon, size = 16, className = '') {
  return `<svg class='${className}' width='${size}' height='${size}'><use href='${_FG_cfg.svg}#${icon}'></use></svg>`;
}

function iOS_getToken(token) {
  if (_FG_user.tokens.indexOf(token) < 0 && typeof token !== 'undefined') {
    ntf_subscribe(token);
  }
}

/**
 * Returns true if versionA is greater than or equal to versionB
 *
 * NOTE: Only supports semantic versioning in the form of major.minor.patch (e.g. 1.2.33)
 * @param {string} versionA
 * @param {string} versionB
 * @returns {boolean}
 */
function isVersionGreaterOrEqualThan(versionA, versionB) {
  var versionANumbers = versionA.split(".").map(function(str) { return Number(str); });
  var versionBNumbers = versionB.split(".").map(function(str) { return Number(str); });

  console.log(`Testing ${versionANumbers} is greater or equal than ${versionBNumbers}`);

  if (versionANumbers.length !== 3) {
    throw TypeError("versionA is not in semantic versioning format of 'major.minor.patch'");
  }

  if (versionBNumbers.length !== 3) {
    throw TypeError("versionB is not in semantic versioning format of 'major.minor.patch'");
  }

  if (versionANumbers[0] > versionBNumbers[0]) {
    return true;
  }

  if (versionANumbers[0] === versionBNumbers[0] && versionANumbers[1] > versionBNumbers[1]) {
    return true;
  }

  return versionANumbers[0] === versionBNumbers[0] && versionANumbers[1] === versionBNumbers[1] && versionANumbers[2] >= versionBNumbers[2];
}

/**
 * Add here any data that will not change on partial reload
 *
 * !It has a rate limitting mechanism of 1 refresh every 60 seconds
 */
function refreshStaticData() {
  const requestInterval = 1000 * 60;  // 60 seconds
  const currentTime = Date.now();
  const lastRequestTime = localStorage.getItem('lastRequestTime');

  const lastTime = lastRequestTime ? parseInt(lastRequestTime, 10) : 0;

  if (isNaN(lastTime) || currentTime - lastTime >= requestInterval) {
    localStorage.setItem('lastRequestTime', currentTime.toString());

    updateBalance();
  }
}

function runAfterPageLoad() {
  if (typeof _FG_user !== 'undefined') {
    updateLastSeen();
  }

  lastForegroundTimestamp = getCurrentTimestamp();
  local_time();

  disconnectEventSeenObservers();

  if (typeof IntersectionObserver !== 'undefined') {
    $('[data-event-seen]').each(function() {
      observeEventSeenAnchor(this);
    });
  }

  if (_FG_cfg.app == 'android' || _FG_cfg.app == 'ios') {
    retryPurchases();
  }

  refreshStaticData();
}

function loadPartial(url, from) {
  sw_close();
  var innerContent = $('#inner-content');
  var inlineJs = $('#inline-js');
  var externalJs = $('#external-js');
  var innerSpinner = $('#inner-content-spinner');
  innerContent.addClass('hide');
  innerSpinner.addClass('loading show');
  destroyInnerAds();
  $.ajax({
    url: url,
    headers: {
      'Partial-Request': true
    },
    success: function (response) {
      var responseHtml = $(response);
      var partialContent = responseHtml.find('#partial-content');
      $('title').text(partialContent.data('title'));
      if (from == 'link') {
        history.pushState(null, null, url);
      }
      currentPath = location.pathname;
      switchNavTab();
      aPage(location.href);
      _FG_cfg.pag = partialContent.data('pag');
      if (typeof adsCfg !== 'undefined') {
        adsCfg.unitname = adsCfg.unitname.replace(/.$/, _FG_cfg.pag == 'feed' ? 'p' : 'n');
      }
      innerContent.html(partialContent.html());
      $('html').scrollTop(0).removeClass(function (index, className) {
        return (className.match(/(^|\s)pag-\S+/g) || []).join(' ');
      }).addClass('pag-' + _FG_cfg.pag);
      inlineJs.html(responseHtml.find('#partial-inline-js').html());
      if (!_FG_cfg.loadedJsScripts.includes(_FG_cfg.pag)) {
        externalJs.append(responseHtml.find('#partial-external-js').html());
        _FG_cfg.loadedJsScripts.push(_FG_cfg.pag);
      } else {
        var pathCallback = window['runAfterPageLoad_' + currentPath.slice(1)];
        if (typeof pathCallback !== 'undefined') {
          pathCallback();
        }
      }
      runAfterPageLoad();
      refreshAds();
      if (window.location.hash) {
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      }
      dispatchLoadPartialEvent();
    },
    complete: function(response) {
      innerContent.removeClass('hide');
      innerSpinner.removeClass('show');
      setTimeout(function() {
        innerSpinner.removeClass('loading');
      }, 250);
    }
  });
}

function dispatchLoadPartialEvent() {
  var eventDetail = {
    url: window.location.href,
    pag: _FG_cfg.pag,
    hash: hash,
    device: _FG_cfg.device,
    communityId: _FG_cfg.user.id_community || null
  };
  const loadPartialEvent = new CustomEvent('loadpartial', {
    detail: eventDetail
  });
  window.dispatchEvent(loadPartialEvent);
}

function switchNavTab() {
  var newTab = $('.menu .btn a[href*="' + location.pathname + '"]').parent();
  if (newTab.length < 1) {
    return;
  }
  var allTabs = $('.menu .btn');
  allTabs.removeClass('active');
  allTabs.each(function() {
    $(this).find('svg use').attr('href', $(this).find('svg use').attr('href').replace('-on', '-off'));
  });
  newTab.addClass('active');
  newTab.find('svg use').attr('href', newTab.find('svg use').attr('href').replace('-off', '-on'));
}

function refreshAds() {
  if (!_FG_cfg.showAds) {
    return;
  }
  if (['md'].includes(_FG_cfg.ads) && isGoogleTagLoaded()) {
    googletag.pubads().refresh();
  }
  if (_FG_cfg.ads == 'md') {
    godo.ads.push(function(x) {
      x.display("[data-ad-lazy=false]");
    });
  }
}

function destroyInnerAds() {
  if (!_FG_cfg.showAds || !isGoogleTagLoaded() || _FG_cfg.ads != 'md') {
    return;
  }
  var innerAds = [];
  $('#inner-content ins, .sw-content ins').each(function() {
    innerAds.push($(this).attr('id'));
  });
  var adSlots = googletag.pubads().getSlots();
  for (i = 0; i < adSlots.length; i++) {
    if (innerAds.includes(adSlots[i].getSlotId().getDomId())) {
      googletag.destroySlots([adSlots[i]]);
    }
  }
}

function isGoogleTagLoaded() {
  if (typeof googletag !== 'object' || typeof googletag.pubads !== 'function') {
    return false;
  }
  return true;
}

function checkCommunity() {
  $.ajax({
    url: 'ajax/community-check',
    success: function(response) {
      if (response.data.commitSha != _FG_cfg.commitSha) {
        fullReload();
      }
      if (response.data.settingsHash != _FG_user.communitySettingsHash) {
        showRefreshToast();
      }
      _FG_user.communitySettingsHash = response.data.settingsHash;
      for (var communityId in response.data.communities) {
        updateOffersBadge(response.data.communities[communityId]);
      }
    },
    error: function(response) {
      if (response.status === 401) {
        fullReload();
      }
    }
  });
}

function getCurrentTimestamp() {
  return Math.floor(Date.now() / 1000);
}

function showRefreshToast() {
  return; // Instead of deleting the code, let's just comment it in case we want it again in the future
  toast(trans('Hay nuevos datos disponibles'), false, 999, 'refresh');
}

function updateOffersBadge(community) {
  var sidebarBadge = $('#sidebar #c-' + community.id + ' .offers-badge');
  var headerBadge = $('.header-menu .pending-offers-badge');
  if (community.offers > 0) {
    sidebarBadge.addClass('show');
    if (_FG_user.id_community == community.id) {
      headerBadge.text(community.offers).addClass('show');
    }
  } else {
    sidebarBadge.removeClass('show');
    if (_FG_user.id_community == community.id) {
      headerBadge.removeClass('show');
    }
  }
}

function loadPlayerVideo(video) {
  if (!video) {
    return;
  }
  var playerVideo = $('#player-video-wrapper');
  if (!playerVideo.length || $('html').height() - playerVideo.offset().top < 100) {
    return;
  }
  if (video.platform == 'dailymotion') {
    if (typeof dailymotion === 'undefined') {
      var scriptElement = document.createElement('script');
      scriptElement.src = 'https://geo.dailymotion.com/libs/player/xh30l.js';
      scriptElement.onload = function() {
        loadDailymotionVideo(video.id);
      }
      document.body.append(scriptElement);
    } else {
      dailymotion.getPlayer()
      .then(function(player) {
        player.destroy();
        loadDailymotionVideo(video.id);
      })
      .catch((e) => console.error(e));
    }
  } else if (video.platform == 'youtube') {
    var iframe = '<iframe src="https://www.youtube.com/embed/' + video.id + '?embed_config=%7B%22adsConfig%22%3A%7B%22adTagParameters%22%3A%7B%22iu%22%3A%22%2F55964524%2Fmd_w%2Fmister%2Fn%22%2C%22cust_params%22%3A%22sz%3D480x360%26t%3DvideoMediaExtra%26cat%3Dstory%26player%3Dyoutube%22%7D%7D%7D&playlist=' + video.id + '&autoplay=1&mute=1&loop=1&controls=0" width="100%" height="300px" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>';
    playerVideo.html(iframe);
  }
}

function loadDailymotionVideo(videoId) {
  dailymotion.createPlayer('player-video-wrapper', {
    video: videoId,
    params: {
      loop: true,
      mute: true,
      customConfig: {
        dynamiciu: '/55964524/md_w/mister/n',
        keyvalues: 'section=players&cat=story&pos=pre-roll&player=dailymotion&t=videoMediaExtra',
        plcmt: '1'
      }
    }
  })
  .catch((e) => console.error(e));
}

function reloadView() {
  popup_close();
  if (swOpened) {
    window.history.back();
  }
  loadPartial(window.location.pathname + window.location.search);
}

function scrollToLastMatch() {
  var parent = $(".gameweek-matches-inline");
  var target = parent.find('[data-status="playing"]').slice(-1);
  if (!target.length) {
    target = parent.find('[data-status="played"]').slice(-1);
  }
  scrollToElement(parent, target);
}

function scrollToSelectedGameweek(parent) {
  var target = parent.find('.selected').slice(-1);
  scrollToElement(parent, target);
}

function scrollToElement(parent, target) {
  if (!parent.length || !target.length) {
    return;
  }
  var offset = parent.width() / 2 - target.width() / 2;
  var targetPosition = target.offset().left - target.parent().offset().left - offset;
  parent.scrollLeft(targetPosition);
}

function executeOrPostponeFunction(fn, ...args) {
  if (typeof window[fn] === 'undefined') {
    setTimeout(function() {
      window[fn](...args);
    }, 1000);
    return;
  }
  window[fn](...args);
}

function debounce(func, timeout = 300){
  console.log("Creating a debounced function");
  let timer;
  return (...args) => {
    console.log("Clearing timer");
    clearTimeout(timer);
    timer = setTimeout(() => { func.apply(this, args); }, timeout);
  };
}

async function fetchCouponWithCode(code) {
  try {
    const response = await fetch(`/api2/store/coupon/${code}`, {
      method: "GET",
      credentials: "same-origin",
      headers: {
        "X-Auth": _FG_cfg.auth,
        "X_REQUESTED_WITH": "xmlhttprequest",
        "Accept": "application/json",
        "Content-Type": "application/json"
      }
    });

    if (response.status >= 400) {
      console.error(`Error fetching Coupon with code <${code}>`);
      return null;
    }

    return await response.json();
  } catch (er) {
    console.error(er);
    return null;
  }
}

/**
 * Coupons
 */
// Note: This one is to make everything work when the user reloads the page
function initializeAutomaticCouponRedeemingRedirection() {
  if (location.hash !== AUTOMATIC_REDEEM_COUPON_REDIRECT_HASH) {
    return;
  }

  const urlSearchParams = new URLSearchParams(window.location.search);
  if (!urlSearchParams.has(AUTOMATIC_REDEEM_COUPON_REDIRECT_QUERY_PARAM_NAME)) {
    return;
  }

  sws.store.post.coupon = urlSearchParams.get(AUTOMATIC_REDEEM_COUPON_REDIRECT_QUERY_PARAM_NAME);
}

function callback_sw_store(post) {
  if (!_FG_cfg.featureCouponsEnabled) {
    return;
  }

  if (post.coupon.length === 0) {
    return;
  }

  const redeemButton = $(".btn-redeem-coupon");
  if (redeemButton.length === 0) {
    return;
  }

  redeemButton.click();
}

function toggleSidebar() {
  var isSidebarOpen = $('html').hasClass('sidebar-open');
  if (!isSidebarOpen) {
    $('html').addClass('sidebar-open no-ptr');
    checkCommunity();
  } else {
    $('html').removeClass('sidebar-open no-ptr').addClass('sidebar-closing');
    setTimeout(function() {
      $('html').removeClass('sidebar-closing');
    }, 200)
  }
}

function add_position(position, amount) {
  amount = parseInt(amount);
  position = parseInt(position);
  var position_element = $('#pos-' + position);
  var content = '<li id="pos-' + position + '" class="btn btn-popup" data-popup="admin/settings/payments/positions/amount" data-position="' + position + '" data-amount="' + amount + '">' +
    '<div class="title">' + position + 'º</div>' +
    '<div class="subtitle">' + thousands(amount) + '</div>' +
    '</li>';
  if (position_element.length > 0 && amount > 0) {
    position_element.replaceWith(content);
  } else if (position_element.length > 0 && amount <= 0) {
    position_element.remove();
  } else if (amount > 0) {
    var ul = $('#admin-payments-positions-list');
    ul.append(content);
    var li = ul.children('li');
    li.detach().sort(function(a, b) {
      if (parseInt($(a).data('position')) > parseInt($(b).data('position'))) {
        return 1;
      } else {
        return -1;
      }
    });
    ul.append(li);
  }
}

function add_member(member_id, member_name, amount) {
  amount = parseInt(amount);
  member_id = parseInt(member_id);
  var member_id_element = $('#member-' + member_id);
  var amountColor = amount > 0 ? 'green' : 'red';
  var content = '<li id="member-' + member_id + '" class="btn btn-popup" data-popup="admin/tools/payments/member" data-member_id="' + member_id + '" data-amount="' + amount + '">' +
    '<div class="title">' + member_name + '</div>' +
    '<div class="subtitle ' + amountColor + '">' + thousands(amount) + '</div>' +
    '</li>';
  if (member_id_element.length > 0 && amount === 0) {
    member_id_element.remove();
  } else if (member_id_element.length > 0) {
    member_id_element.replaceWith(content);
  } else if (amount !== 0) {
    var ul = $('#admin-payments-members');
    ul.append(content);
    var li = ul.children('li');
    li.detach().sort(function(a, b) {
      if (parseInt($(a).data('amount')) > parseInt($(b).data('amount'))) {
        return -1;
      } else {
        return 1;
      }
    });
    ul.append(li);
  }
}

function callback_admin_setting(response) {
  if (response.key == 'community_icon') {
    changeSidebarCommunityIcon(response.value);
  }

  if (response.key == 'is_captain_enabled') {
    return true;
  }

  reloadSW();
}

function changeSidebarCommunityIcon(emoji) {
  var emojiElement = $('#sidebar .communities .active .emoji');
  emojiElement.text(emoji);
  if (emoji.length === 0) {
    emojiElement.text(_FG_user.communities[_FG_user.id_community].flag_emoji);
  }
}

function callback_admin(response) {
  var exit = false;
  if (response.action == 'reset-points') {
    exit = 'standings';
  } else if (response.action == 'reset-all') {
    exit = 'feed';
  } else if (response.action == 'cancel_transfers') {
    exit = 'feed';
  } else if (response.action == 'remove') {
    toast(trans('Liga eliminada'));
    exit = 'feed';
  } else if (response.action == 'recover') {
    toast(trans('Email enviado'));
  }
  if (exit) {
    return exit;
  }
  reloadSW();
}

function callback_other(response) {
  var exit = false;
  if (response.action == 'leave') {
    exit = '/feed';
  }
  return exit;
}

function callback_toggle_admin(response, btn) {
  reloadSW();
}

function check_admin_name(e) {
  if (e.value === '') {
    input_error(e, trans('Nombre incorrecto'));
  } else {
    input_ok(e);
  }
}

function showAjaxErrorToast(response) {
  var errorMsg = trans('¡Ups! Algo ha fallado. Revisa la acción y vuelve a intentarlo');
  if (response.status == 404) {
    errorMsg = trans('ERROR_404_TOAST');
  } else if (typeof response.responseJSON !== 'undefined' && typeof response.responseJSON.msg !== 'undefined') {
    errorMsg = response.responseJSON.msg;
  }
  toast(errorMsg, 'red');
}
function check_admin_market_players(e) {
  var maxPlayers = _FG_cfg.user.id_competition == 15 ? 50 : 20;
  var value = parseInt(e.value.replace(/[\.|,|\s]/g, ''));
  if (value < 0 || value > maxPlayers) {
    input_error(e, trans('Valor incorrecto'));
  } else {
    input_ok(e);
  }
}

function check_admin_prizes_points(e) {
  var value = parseInt(e.value.replace(/[\.|,|\s]/g, ''));
  if (value < 0 || value > 1000000) {
    input_error(e, trans('Valor incorrecto'));
  } else {
    input_ok(e);
  }
}

function check_admin_prizes_positions(e) {
  var value = parseInt(e.value.replace(/[\.|,|\s]/g, ''));
  if (value < 0 || value > 10000000) {
    input_error(e, trans('Valor incorrecto'));
  } else {
    input_ok(e);
  }
}

function check_admin_prizes_best_xi(e) {
  check_admin_prizes_positions(e);
}

function check_admin_prizes_goals(e) {
  check_admin_prizes_points(e);
}

function check_admin_prizes_fixed(e) {
  check_admin_prizes_positions(e);
}

function check_admin_pools_prize(e) {
  var value = parseInt(e.value.replace(/[\.|,|\s]/g, ''));
  if (value < 10000 || value > 300000) {
    input_error(e, trans('Valor incorrecto'));
  } else {
    input_ok(e);
  }
}

function check_admin_payments_member(e) {
  var value = parseInt(e.value.replace(/[\.|,|\s]/g, ''));
  if (value < -10000000 || value > 10000000) {
    input_error(e, trans('Valor incorrecto'));
  } else {
    input_ok(e);
  }
}

function showGoodbyeAdsCta() {
  return false;
  if (_FG_cfg.brand.internalName != 'mister') {
    return false;
  }
  var ctaSeenCookie = Cookies.get('goodbye-ads-seen');
  var ctaClickCookie = Cookies.get('goodbye-ads-click');
  if (ctaSeenCookie || ctaClickCookie) {
    return false;
  }
  return true;
}

function clickedGoodbyeAds() {
  Cookies.set('goodbye-ads-click', 1, {expires: 365});
}

function fullReload() {
  var ptr = $('.pull-to-refresh');
  ptr.addClass('rotating force');
  setTimeout(function() {
    location.reload();
  }, 10);
}

function showRevolutPromo() {
  var revolutPromoSeenCookie = Cookies.get('revolut-promo-seen');
  if (!_FG_cfg.showRevolutPromo || revolutPromoSeenCookie) {
    return false;
  }
  return true;
}

function chromeTabsOpenUrlCallback(error, result) {
  if (error) {
    console.log("[ERROR] chromeTabsOpenUrlCallback:" + JSON.stringify(error));
  }
}

function cbLoginURLCallback(error, result) {
  if (error) {
    console.log("[ERROR] cbLoginURLCallback:" + JSON.stringify(error));
  }
}
