$('body').on('ontouchstart' in document ? 'touchstart' : 'mouseenter', '.btn', function (e) {
  $(this).addClass('tap');
});

$('body').on('ontouchend' in document ? 'touchend touchmove touchcancel' : 'mouseleave', '.btn', function (e) {
  $(this).removeClass('tap');
});

$('body').on('click', 'a[href="action/logout"]', function (e) {
  e.preventDefault();

  if (localStorage.getItem("facebook-sign-in") !== null) {
    console.log("It's Facebook sign in");
    localStorage.removeItem("facebook-sign-in");

    if (_FG_cfg.app == 'android') {
      MRInterface.FacebookLogout();
    }
  }

  if (ChampionCommon && ChampionCommon.logout) {
    ChampionCommon.logout();
  }

  window.location = "/action/logout";
});

$('body').on('click', '.btn-popup', function () {
  var btn = $(this);
  if (btn.hasClass('popup-navigate')) {
    popup_content = $('#popup-content').clone(true);
  }
  if ($('.sw-admin').length > 0 && _FG_data.other.admin === 0) {
    if (btn.hasClass('btn-admin-leave-league')) {
      popup_open(btn.data('popup'), btn.data(), btn, btn.data('preload'));
    } else {
      toast(trans('Solo los administradores pueden editar la configuración'), 'red');
    }
  } else {
    popup_open(btn.data('popup'), btn.data(), btn, btn.data('preload'));
  }
});

$('body').on('click', '.btn-clauses-inactive', function(){
  toast(trans('Tienes que tener activo el uso de cláusulas de rescisión para modificar esta configuración'), 'red');
});

$('body').on('click', '.popup-back', function() {
  popup_back();
});

$('#popup').on('submit', 'form', function () {
  return false;
});

window.onpopstate = function (event) {
  if (popup) {
    popup_close();
  }
};

$('body').on('click', '#popup, #btn-store-floating', function (e) {
  e.stopPropagation();
});

$('body').on('click', '.btn-sw', function () {
  var btn = $(this);
  var sw = btn.data('sw');
  var url = btn.data('url');
  if (typeof url !== 'undefined') {
    window.location.hash = hash = url;
  } else {
    if (Object.keys(btn.data()).length > 1 && typeof sws[sw] !== 'undefined') {
      sws[sw].extra = btn.data();
    }
    window.location.hash = hash = sw;
  }
});

window.onhashchange = function (e) {
  if (window.location.hash.startsWith('#gameweek-match-') ||
      window.location.hash == '#google_vignette' ||
      window.location.hash == '#goog_rewarded') {
    return;
  }
  var new_hash = window.location.hash.replace('#', '');
  var post;
  if (new_hash.length === 0 || new_hash == '_=_') {
    aPage(location.href);
    sw_close();
  } else {
    aPage(location.href);
    if (new_hash in sws) {
      post = sws[new_hash].post;
      if(new_hash === 'subs') {
        post.id_gameweek = _FG_data.gameWeekId;
      }
      sw_open(new_hash, post);
    } else if (
      new_hash.indexOf('players/') === 0 ||
      new_hash.indexOf('teams/') === 0 ||
      new_hash.indexOf('users/') === 0 ||
      new_hash.indexOf('matches/') === 0 ||
      new_hash.indexOf('comments/') === 0 ||
      new_hash.indexOf('contact') === 0 ||
      new_hash.indexOf('pool-') === 0 ||
      new_hash.indexOf('gameweek') === 0
    ) {
      var url = new_hash.split('/');
      var pag = url[0];
      var id = url[1];
      var slug = url[2];
      var qs = url[3];
      if (window.location.href.indexOf('?gw=') > -1) {
        qs = window.location.href.replace(/(.*)(gw=\d+)(.*)/, '$2');
      }
      post = {
        post: pag,
        id: id,
        slug: slug,
        qs: qs,
        comments: 0
      };
      if (pag == 'comments' && $('#feed-' + id).length) {
        post.comments = $('#feed-' + id).data('comments');
      }
      sw_open(pag, post);
    } else if (new_hash.indexOf('admin') === 0) {
      sw_open(new_hash, 'admin');
    } else if (new_hash != 'substitution') {
      sw_open(new_hash, post);
    }
  }
  hash = new_hash;

  if (sessionStorage.getItem('should_reload_view') !== null) {
    sessionStorage.removeItem('should_reload_view');
    reloadView();
  }
};

$('#toast').on('click', function () {
  toast_close();
});

$('#toast').on('click', '.btn-undo', function () {
  if (undo) {
    var btn = $(this);
    btn_lock(btn);
    undo.data.undo = true;
    $.ajax({
      url: undo.url,
      data: undo.data,
      success: function (response) {
        callback_sub_do(response, false);
        toast(trans('Acción deshecha'));
        undo = false;
      },
      complete: function () {
        btn_unlock(btn);
      }
    });
  }
});

$('#toast').on('click', '.btn-refresh', function () {
  fullReload();
});

$('body').on('change', '#sw-market-filter', function() {
  var form = $(this);
  form.find('select').prop('disabled', true);
  _FG_data.market.interval = form.find('select[name=interval]').val();
  load_market(_FG_data.market);
});

$('#popup').on('input', 'input.check', function () {
  var val = $(this).val().toLowerCase();
  var word = $(this).data('word').toLowerCase();
  $('#btn-send').prop('disabled', (val != word));
});

$('#verify-email').click(function () {
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/verify',
    data: btn.data(),
    success: function (response) {
      if (response.status == 'ok') {
        toast(trans('Email de verificación enviado'));
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-promo-dismiss', function(e) {
  hidePromo(e);
});

$('body').on('click', '#purchase-button', function () {
  btn_purchase = $(this);
  btn_lock(btn_purchase);
  var community = _FG_user.id_community;
  var id_uc = btn_purchase.data('id');
  var sku = btn_purchase.data('sku');
  var externalCheckout = btn_purchase.data('external-checkout') == true;
  var ts = Math.floor(new Date().getTime() / 1000);
  var promo = parseInt(btn_purchase.data("promo"), 10);
  if (isNaN(promo)) {
    promo = null;
  }

  setCurrentPurchasePromo(promo);

  if (externalCheckout) {
    startExternalCheckout(sku, promo, community, id_uc);
    return;
  }
  if (_FG_cfg.app == 'android') {
    startAndroidPurchase(sku, id_uc, ts);
    return;
  }
  if (_FG_cfg.app == 'ios') {
    startIOSPurchase(sku);
    return;
  }

  startPayment(sku, promo, community, id_uc).then(function(response) {
    if (response.URL && response.URL != "") {
      location.href = response.URL;
    } else {
      toast(trans("Algo ha fallado"), 'red');
      btn_unlock(btn_purchase);
    }
  }).catch(function() {
    toast(trans("No se pudo iniciar la compra"), 'red');
    btn_unlock(btn_purchase);
  });
});

$('body').on('click', '#paypal-button-container', function () {
  btn_purchase = $(this);
  btn_lock(btn_purchase);
  var community = _FG_user.id_community;
  var iduc = _FG_user.id_uc;
  var sku = btn_purchase.data('sku');
  var promo = parseInt(btn_purchase.data("promo"), 10);
  if (isNaN(promo)) {
    promo = null;
  }

  startPayment(sku, promo, community, iduc).then(function(response) {
    btn_unlock(btn_purchase);
    if (!response.URL || response.URL == "") {
      toast(trans("Algo ha fallado"), 'red');
      return;
    }
    if (_FG_cfg.app == 'ios') {
      $('#paypal-button-container').hide();
      $('#paypal-link-container').css('display', 'flex').attr('href', response.URL);
    } else {
      location.href = response.URL;
    }
    updateMoneyUntilChange();
  }).catch(function() {
    toast(trans("No se pudo iniciar la compra"), 'red');
    btn_unlock(btn_purchase);
  });
});

$('body').on('click', '.btn-sw-link', function (e) {
  e.preventDefault();
  var btn = $(this);
  window.location.hash = hash = btn.attr('href');
});

$('body').on('click', '.btn-dd', function (e) {
  var btn = $(this);
  var id = btn.data('dd');
  $('.' + id).toggle();
  var state = $('.' + id).is(':visible') ? 'close' : 'open';
  btn.removeClass('open close').addClass(state);
});

$('.alert-app .btn-close').click(function () {
  Cookies.set('alert_app', 1, { expires: 7, path: '/' });
  $('.alert-app').remove();
});

$('body').on('click', '.btn-share', function () {
  var btn = $(this).data();
  if (_FG_cfg.app == 'android') {
    MRInterface.openShareDialog(btn.title, btn.text, btn.url);
  } else if (_FG_cfg.app == 'ios') {
    window.webkit.messageHandlers.openShareDialog.postMessage(JSON.stringify(btn));
  } else {
    navigator.share({
      title: btn.title,
      text: btn.text,
      url: btn.url,
    });
  }
});

$('body').on('click', '.settings-options label', function () {
  var input = $(this).parent().find('input');
  var checked = input.attr('type') == 'radio' ? true : !input.prop('checked');
  input.prop('checked', checked);
});

$('body').on('click', '.btn-toggle input', function () {
  var input = $(this);
  var btn = input.closest('.btn-toggle');
  var key = btn.data('key');
  var value = +input.prop('checked');
  var url = btn.data('url');
  if (url) {
    var text;
    var post = {
      key: key,
      value: value
    };
    $.ajax({
      url: 'ajax/' + url,
      data: post,
      success: function (response) {
        if (response.status == 'ok') {
          if (typeof window['callback_' + url.replace(/-|\//g, '_')] === 'function') {
            window['callback_' + url.replace(/-|\//g, '_')](response.data);
          } else {
            if (parseInt(response.data.value) == 1) {
              text = (btn.find('.subtitle').text() == trans('Desactivado')) ? trans('Activado') : trans('YES');
              btn.find('.subtitle').text(text);
            } else {
              text = (btn.find('.subtitle').text() == trans('Activado')) ? trans('Desactivado') : trans('NO');
              btn.find('.subtitle').text(text);
            }
          }
        }
      }
    });
  }
});

$('#popup').on('input', '#clause_range', function () {
  var input = $(this);
  var multipliers = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0];
  var val = parseInt(input.val());
  var floor = parseFloat(input.data('floor'));
  var clause = parseInt(floor * multipliers[val]);
  var def = parseInt(input.data('default'));
  var cost = Math.floor(floor * 0.2 * (val - def));
  var clauseSku = 'clause_t' + input.data('clause_tier') + '_s' + (val - def);
  var shield = $('input[name="shield"]').val();
  sendEvent('select_clause');
  $('.top .range-value').text((val == 6) ? trans('Blindado') : thousands(clause));
  if (val > def) {
    $('.bottom .swipe').hide();
    $('.bottom .pay').show();
    $('.bottom .separator p').show();
    if (val == 6) {
      $('.btn-pay, .btn-purchase, .btn-credits').hide();
      $('.btn-purchase-shield').show();
      $('.bottom .separator p').text(trans('Blindar jugador durante…'));
      if (shield != 0) {
        $('.bottom .separator p').hide();
      }
      updateFutureBalance(0);
    } else {
      $('.btn-purchase-shield').hide();
      $('.btn-pay').show().html(getSVG('balance') + ' ' + thousands(cost));
      $('.bottom .separator p').text(trans('Subir cláusula pagando…'));
      $('.btn-purchase-clause').show().data('sku', clauseSku);
      $('.btn-purchase-clause').html(getSVG('coin') + ' ' + _FG_cfg.sku_prices[clauseSku]);
      updateFutureBalance(cost);
    }
  } else if (val == def) {
    $('.bottom .pay').hide();
    $('.bottom .swipe').show();
    updateFutureBalance(cost);
  } else {
    $('.bottom .swipe').hide();
    $('.bottom .pay').show();
    $('.btn-pay').show().html(getSVG('balance') + ' ' + thousands(Math.abs(Math.floor(cost / 2))));
    $('.bottom .separator p').show().text(trans('Bajar cláusula e ingresar…'));
    $('.btn-purchase, .btn-credits').hide();
    updateFutureBalance(Math.abs(Math.floor(cost / 2)) * -1);
  }
});

$('#popup').on('input', '#pool_range', function() {
  var input = $(this);
  var val = parseInt(input.val());
  var prices = input.data('prices');
  var prizes = input.data('prizes');
  prices.forEach(function(price) {
    if (price.step == val) {
      selectedPrice = price.price;
    }
  });
  prizes.forEach(function(prize) {
    var prizeDiv = $('#hits-' + prize.hits);
    prizeDiv.find('.prize').html(getSVG('coin') + ' ' + selectedPrice * prize.multiplier);
  });
  $('#play-pool').html(trans('Participar') + ' ' + getSVG('coin') + selectedPrice);
});

$('#popup').on('click', '.form-range .btn-pay', function () {
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/clause-set',
    data: $('#popup form').serialize(),
    success: function (response) {
      if (response.status == 'ok') {
        $('.item-clause .value').text(thousands(Math.round(response.data.newClause)));
        toast(trans('Cláusula modificada'));
        updateBalance(response.data.balance);
        popup_close();
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('#popup').on('click', '#btn-send', function () {
  var btn = $(this);
  btn_lock(btn);
  var exit = false;
  var ajax = btn.data('ajax');
  var interstitials = ["bid", "clause-pay", "sale", "loanable"];
  var popupForm = $('#popup form');
  var serializedPopupForm = popupForm.serialize();
  var popupFormData = new FormData(popupForm.get(0));

  const isReset = ajax === 'admin' &&
    ['reset-all', 'reset-points', 'reset-teams'].indexOf(popupFormData.get('action')) !== -1;

  if (isReset) {
    const action = popupFormData.get('action').replace('-', '/')
      .replace('/all', '')
      .replace('points', 'ranking')
      .replace('teams', 'rosters');

    const url    = `api2/${_FG_cfg.user.id_community}/${action}`
    const data   = action === 'reset/ranking' ? {} : { option: popupFormData.get('reset_options') };

    $.ajax({
      method: 'PUT',
      data: data,
      url: url,
      success: function(response) {
        if (interstitials.indexOf(ajax) !== -1 ) {
          showInterstitial(ajax);
        }

        if (action === 'reset/ranking') {
          window.location.href = _FG_cfg.base + 'standings';
        }

        if (action === 'reset/rosters') {
          window.location.href = _FG_cfg.base + 'team';
        }

        if (action === 'reset') {
          window.location.href = _FG_cfg.base + 'feed';
        }
      },
      complete: function (response) {
        btn_unlock(btn);
      }
    });

    return;
  }

  $.ajax({
    url: 'ajax/' + ajax,
    data: serializedPopupForm,
    success: function (response) {
      if (interstitials.indexOf(ajax) !== -1 ) {
        showInterstitial(ajax);
      }
      if (response.status == 'ok') {
        if (typeof window['callback_' + ajax.replace(/-|\//g, '_')] === 'function') {
          exit = window['callback_' + ajax.replace(/-|\//g, '_')](response.data, popupFormData);
        }
        if (!exit) {
          popup_close();
        }
      } else if (response.status == 'navigate') {
        popup_content = $('#popup-content').clone(true);
        popup_open(response.popup, response.data);
      }
    },
    complete: function () {
      if (exit) {
        if (typeof exit !== 'string' || exit.indexOf('#') > -1) {
          fullReload();
        } else {
          window.location.href = _FG_cfg.base + exit;
        }
      } else {
        btn_unlock(btn);
      }
    }
  });
});

$('#popup').on('change', '#upload', function () {
  var file = this.files[0];
  if (file.size > 8388608 || file.type.indexOf('image/') == -1) {
    toast(trans('El archivo es muy grande o no es una imagen'), 'red');
    $('#btn-send-pic').prop('disabled', true);
  } else {
    $('#btn-send-pic').prop('disabled', false);
    $('#btn-send-pic').show();
    $('#preview').show();
    $('.choose').hide();
    $('.footer .buttons').addClass('btn-2');
  }
});

$('#popup').on('click', '.btn-rotate', function () {
  $('#preview').cropit('rotateCW');
});

$('#popup').on('click', '#btn-send-pic', function () {
  var post = {
    pic: $('#preview').cropit('export', {
      type: 'image/png',
    })
  };
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/picture',
    data: post,
    success: function (response) {
      window.location.href = _FG_cfg.base + 'feed';
    }
  });
});

$('body').on('click', '.btn-pic-fb, .btn-pic-delete', function () {
  var btn = $(this);
  btn_lock(btn);
  var post = {
    pic: btn.data('pic')
  };
  $.ajax({
    url: 'ajax/picture',
    data: post,
    success: function (response) {
      window.location.href = _FG_cfg.base + 'feed';
    }
  });
});

function fromLoanRangeInputToDays(value) {
  if (value == 1) {
    return 3;
  }

  if (value == 2) {
    return 7;
  }

  if (value == 3) {
    return 14;
  }

  return 28;
}

$('#popup').on('input', '#range-loan', function () {
  const days = fromLoanRangeInputToDays(this.value);
  $('.range-value').text(trans('DAYS_N', { daysCount: days }));
  update_popup_send();
  $('input[name="days"]').val(days);

  if (_FG_user.loans_floor > 0) {
    const marketValue = $('#player-value').val();
    const newMinOfferValue = ((marketValue * _FG_user.loans_floor / 100) * days);
    const formattedInput = $('#input-tel');
    const valueInput = $('#input-range');

    formattedInput.val(thousands(newMinOfferValue));
    valueInput.val(newMinOfferValue);
  }
});


$('body').on('click', '.btn-shield', function () {
  toast(trans('EXPIRES_IN_N', { amount: $(this).data('shield') }));
});

$('body').on('click', '.btn-favorite', function (e) {
  e.preventDefault();
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/favorite',
    data: btn.data(),
    success: function (response) {
      if (response.status == 'ok') {
        if (response.action == 'add') {
          svgChangeIcon(btn, 'star-filled');
          if (btn.data('type') == 'player') {
            toast(trans('Jugador añadido a la lista de favoritos'));
          } else if (btn.data('type') == 'team') {
            toast(trans('Equipo añadido a la lista de favoritos'));
          }
          btn.data('action', 'remove');
        } else if (response.action == 'remove') {
          svgChangeIcon(btn, 'star');
          if (btn.data('type') == 'player') {
            toast(trans('Jugador eliminado de la lista de favoritos'));
          } else if (btn.data('type') == 'team') {
            toast(trans('Equipo eliminado de la lista de favoritos'));
          }
          btn.data('action', 'add');
        }
        if (btn.data('from') == 'list') {
          btn.closest('li').remove();
        }
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-fb-logout', function () {
  if (_FG_cfg.app == 'android') {
    MRInterface.FacebookLogout();
  } else if (_FG_cfg.app == 'ios') {
    window.webkit.messageHandlers.FacebookLogout.postMessage(1);
  }
});

$('body').on('click', "#fg-content a[href*='" + _FG_cfg.base + "'], #sidebar a[href*='" + _FG_cfg.base + "']", function () {
  if ($(this).data('partial')) {
    return;
  }
});

$('body').on('click', '[data-tab]', function () {
  var btn = $(this);
  var tabs = btn.closest('[data-panels]');
  var panels = tabs.data('panels');
  $('.panels-' + panels + ' > .panel').hide();
  $('.panels-' + panels + ' .panel-' + btn.data('tab')).show();
  tabs.find('[data-tab]').removeClass('active');
  btn.addClass('active');
  _FG_cfg.active_tabs[panels] = btn.data('tab');
});

$('body').on('click', '.navbar-switch-tab', function (e) {
  showInterstitial('navbar-switch-tab');//I will show above webview
  return true;
});

$('body').on('click', '#play-pool', function(){
  console.log("playing pool");
  var btn = $(this);
  btn_lock(btn);
  var poolValues = $('#pool_range');
  var id_competition = poolValues.data("idcompetition");
  var id_gameweek = poolValues.data("idgameweek");
  var step = parseInt(poolValues.val());
  console.log(id_competition);
  console.log(id_gameweek);
  console.log(_FG_user.id);
  console.log(step);

  $.ajax({
    type: "POST",
    url: '/api2/pool/buypoolentry',
    contentType: "application/json",
    dataType: "json",
    data: JSON.stringify({
      competitionID: id_competition,
      gameweekID: id_gameweek,
      idUser: _FG_user.id,
      step: step
    }),
    success: function(response) {
      if (response.status == "OK"){
        toast(trans('Compra realizada'));
        updateCredits(_FG_user.credits - response.spent);
        btn_pools.trigger('click');
        popup_close();
      } else if(response.status == "KO"){
        if (response.msg == "Not enough credits"){
          popup_open('credits-get', {}, false);
        }
      }
    },
    complete: function(response) {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-credits', function () {
  var exit = false;
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/credits',
    data: btn.data(),
    success: function (response) {
      toast(trans('Compra realizada'));
      updateCredits(response.data.credits);
      popup_close();
      sendEvent('spend_virtual_currency', { sku: response.data.sku, currency: 'coin' });
      if (response.data.sku == 'change_player' && btn.data('slot')) {
        $.ajax({
          url: 'ajax/sub-do',
          data: btn.data(),
          success: function (response) {
            // analytics.logEvent('coins_spent_change', {
            //  credits: 50
            // });
            callback_sub_do(response, true);
          },
          complete: function () {
            btn_unlock(btn);
          }
        });
      } else if (response.data.sku.indexOf('change_player') > -1) {
        _FG_cfg.user.changes = response.data.subs;
        if (hash == 'subs') {
          reloadSW();
        } else {
          $('.list-subs-purchase').removeClass('list-subs-purchase').addClass('list-subs-open');
          $('.list-subs-open .player').removeClass('btn-credits');
          $('.subs-qty span').text(trans('SUBSTITUTION_AVAILABLE_CHANGES', { changesCount: _FG_cfg.user.changes }));
          $('.subs-qty .price').hide();
        }
      } else if (response.data.sku.indexOf('add_formation_pack') > -1) {
        exit = 'team?lineup=global';
      } else if (response.data.sku.indexOf('add_formation') > -1) {
        _FG_data.formations[btn.data('formation')] = 'free';
        $.ajax({
          url: 'ajax/formation',
          data: btn.data(),
          success: function (response) {
            if (response.status == 'ok') {
              location.replace('team?lineup=global');
            }
          }
        });
      } else if (response.data.sku == 'remove_ads') {
        exit = 'feed';
      } else if (response.data.sku.indexOf('add_formation') > -1) {
        var formation = response.data.sku.replace('add_formation_', '').replace(/_/g, '-');
        var data_sku = (response.data.sku == 'add_formation_pack') ? '' : '[data-sku="' + response.data.sku + '"]';
        $('.btn-purchase' + data_sku).removeClass('btn-purchase').addClass('btn-free');
        if (formation == 'pack' || formation == 'pack-community') {
          $('.btn-pack').remove();
          for (var key in data.formations) {
            _FG_data.formations[key] = 'free';
          }
          $('.btn-purchase').removeClass('btn-purchase').addClass('btn-free');
        } else {
          _FG_data.formations[formation] = 'free';
        }
      } else if (response.data.sku.indexOf('clause') > -1) {
        $('.item-clause .value').text(thousands(Math.round(response.data.newClause)));
        toast(trans('Cláusula modificada'));
      } else if (response.data.sku.indexOf('shield') > -1) {
        btn_clause = $('.bottom-actions .btn-clause');
        btn_clause.text(trans('Blindado'));
        btn_clause.data('shield', response.data.shield);
        toast(trans('Jugador blindado'));
      } else if (response.data.sku == 'rescind') {
        toast(trans('Jugador vendido'));
        exit = 'feed';
      } else if (response.data.sku == 'show_bids') {
        exit = 'market';
      } else if (response.data.sku.indexOf('porras_entry') > -1) {
        $('.porra .btn-popup').removeClass('btn-popup').addClass('btn-porra').text('Enviar resultado');
        $.ajax({
          url: 'ajax/porra',
          data: $('#form-porra').serialize(),
          success: function (response) {
            if (response.status == 'ok') {
              toast(trans('Resultado guardado'), 'green');
            }
          },
        });
      } else if (response.data.sku == 'salaries') {
        $('.salaries .btn').removeClass('btn-popup btn--primary').addClass('btn--accent').text(trans('Pagado'));
        $('.salaries .amount').removeClass('red').addClass('paid');
      } else if (response.data.sku == 'auto_lineup') {
        const memberId = _FG_user.id_uc;
        const gameweekId = _FG_data.gameWeekId;
        sendAutoLineupRequest(memberId, response.data.auto_lineup_order, gameweekId);
      }
    },
    complete: function (response) {
      if (response.responseJSON.popup) {
        popup_open(response.responseJSON.popup, {}, false);
      } else if (exit) {
        if (exit.indexOf('#') > -1) {
          window.location.hash = exit;
        } else {
          window.location.href = _FG_cfg.base + exit;
        }
      }
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-pools', function () {
  btn_pools = $(this);
  btn_lock(btn_pools);
  $.ajax({
    url: 'ajax/pools',
    data: btn_pools.data(),
    success: function (response) {
      if (response.status == 'ok') {
        btn_pools.parent().find('.btn').removeClass('mine');
        btn_pools.addClass('mine');
        $('.user-not-participate').addClass('user-participate').removeClass('user-not-participate');
        $('.pool-info').text(trans('POOL_PARTICIPATION_INFO', { date: $('.pool-games .date').first().text() }));
      }
    },
    complete: function (response) {
      if (response.responseJSON.popup) {
        if (typeof response.responseJSON.data !== "undefined"){
          popup_open(response.responseJSON.popup, response.responseJSON.data, false);
        } else{
          popup_open(response.responseJSON.popup, {}, false);
        }
      }
      btn_unlock(btn_pools);
    }
  });
});

$('body').on('click', '.btn-save-prediction', function () {
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/pools',
    data: $('#prediction').serialize(),
    success: function (response) {
      if (response.status == 'ok') {
        toast(trans('Predicción guardada'), 'green');
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-porra', function () {
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/porra',
    data: $('#form-porra').serialize(),
    success: function (response) {
      if (response.status == 'ok') {
        toast(trans('Resultado guardado'), 'green');
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-check-porra', function () {
  var btn = $(this);
  var proceed = {
    goals: false,
    scorer: false
  };
  var inputs = {
    goals_home: $('#form-porra input[name=goals_home]'),
    goals_away: $('#form-porra input[name=goals_away]'),
    id_scorer: $('#form-porra select[name=id_scorer]')
  };
  if (!$.isNumeric(inputs.goals_home.val()) || !$.isNumeric(inputs.goals_away.val())) {
    proceed.goals = false;
    toast(trans('El resultado no es válido'), 'red');
    inputs.goals_home.addClass('error');
    inputs.goals_away.addClass('error');
  } else {
    proceed.goals = true;
    inputs.goals_home.removeClass('error');
    inputs.goals_away.removeClass('error');
  }
  if (inputs.id_scorer.find(':selected').val() < 0) {
    proceed.scorer = false;
    toast(trans('El goleador no es válido'), 'red');
    inputs.id_scorer.addClass('error');
  } else {
    proceed.scorer = true;
    inputs.id_scorer.removeClass('error');
  }
  if (proceed.goals && proceed.scorer) {
    btn.addClass('btn-popup').removeClass('btn-check-porra').trigger('click');
  }
});

$('body').on('click', '.sw-store .packs .box', function () {
  var index = $(this).index();
  var sku = $(this).data('sku');
  var promo = $(this).data('promo');
  var platform = $(this).data('extra') ? 'mobile' : 'paypal';
  var parent_FG_cfg = $('.sw-store .packs');
  var parent_mobile = $('.sw-store .panel-mobile');
  var parent_paypal = $('.sw-store .panel-paypal');
  var item_mobile, item_paypal;
  parent_FG_cfg.find('.box').removeClass('selected');
  parent_FG_cfg.find('input').prop('checked', false);
  if (platform == 'mobile') {
    $(this).addClass('selected').find('input').prop('checked', true);
    item_paypal = parent_paypal.find('.box:eq(' + index + ')');
    item_paypal.addClass('selected').find('input').prop('checked', true);
    parent_mobile.find('.btn-purchase').data('sku', sku);
    parent_mobile.find('.btn-purchase').data('promo', promo);
    parent_paypal.find('.btn-purchase').data('sku', item_paypal.data('sku'));
    parent_paypal.find('.btn-purchase').data('promo', item_paypal.data('promo'));
    parent_mobile.find('.pay-w-paypal .free-credits').text($(this).data('extra'));
  } else {
    $(this).addClass('selected').find('input').prop('checked', true);
    item_mobile = parent_mobile.find('.box:eq(' + index + ')');
    item_mobile.addClass('selected').find('input').prop('checked', true);
    parent_paypal.find('.btn-purchase').data('sku', sku);
    parent_paypal.find('.btn-purchase').data('promo', promo);
    parent_mobile.find('.btn-purchase').data('sku', item_mobile.data('sku'));
    parent_mobile.find('.btn-purchase').data('promo', item_mobile.data('promo'));
    parent_mobile.find('.pay-w-paypal .free-credits').text(item_mobile.data('extra'));
  }
  if (_FG_cfg.app == 'ios') {
    $('#paypal-button-container').show();
    $('#paypal-link-container').hide();
  }
});

$('body').on('click', '.pay-w-paypal', function () {
  $('.panel-mobile').hide();
  $('.panel-paypal').show();
  $('.panel-credits').find('[data-tab="mobile"]').removeClass('active');
  $('.panel-credits').find('[data-tab="paypal"]').addClass('active');
});

$('body').on('click', '#paypal-link-container', function () {
  $('#paypal-button-container').show();
  $(this).hide();
});

$('body').on('change', '.pool-gw-selector select', function () {
  var pag = 'pool-' + $(this).data('pool');
  post = {
    post: pag,
    id: this.value,
  };
  sw_open(pag, post);
});

$('body').on('click', '.pool-table .btn', function () {
  var pag = 'pool-private';
  post = {
    post: pag,
    id: $(this).data('id_gameweek'),
    id_uc: $(this).data('id_uc'),
    name: $(this).data('name')
  };
  sw_open(pag, post);
});

$('body').on('click', '.btn-players-all', function () {
  $(this).hide();
  $(this).parent().find('.btn-players-mine').show();
  $(this).parent().find('.players-mine').hide();
  $(this).parent().find('.players-all').css('display', 'flex');
});

$('body').on('click', '.btn-players-mine', function () {
  $(this).hide();
  $(this).parent().find('.btn-players-all').show();
  $(this).parent().find('.players-all').hide();
  $(this).parent().find('.players-mine').css('display', 'flex');
});

$('body').on('click', '.btn-popup.btn-info', function (e) {
  e.stopPropagation();
});

$('body').on('input', '.check-on-input', function() {
  eval($(this).attr('onblur'));
});

$('.sw').on('scroll', function () {
  if ($('.sw').scrollTop() > 0) {
    $('.sw-topbar').addClass('shadow');
  } else {
    $('.sw-topbar').removeClass('shadow');
  }
});

$(window).on('scroll', function () {
  var headerToShadow = (typeof _FG_user !== 'undefined' && _FG_user.nav_bottom) ? '.header-top' : '.header-menu';
  if ($(window).scrollTop() > 0) {
    $(headerToShadow).addClass('shadow');
  } else {
    $(headerToShadow).removeClass('shadow');
  }
});

$('body').on('click', '.btn-copy', function() {
  var input = $('#' + $(this).data('input'));
  if (_FG_cfg.app.indexOf('ios') > -1) {
    var el = input.get(0);
    var oldContentEditable = el.contentEditable,
      oldReadOnly = el.readOnly,
      range = document.createRange();
    el.contenteditable = true;
    el.readonly = false;
    range.selectNodeContents(el);
    var s = window.getSelection();
    s.removeAllRanges();
    s.addRange(range);
    el.setSelectionRange(0, 999999);
    el.contentEditable = oldContentEditable;
    el.readOnly = oldReadOnly;
  } else {
    input.select();
  }
  document.execCommand('copy');
  toast(trans('Enlace copiado'));
});

$('body').on('click', '.go-back', function() {
  if (history.length <= 1) {
    window.location = window.location.pathname;
  } else {
    history.back();
  }
});

$('body').on('click', '.popup-close', function() {
  popup_close();
});

$('body').on('click', '.btn-player-gw', function(e) {
  e.preventDefault();
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/player-gameweek',
    data: btn.data(),
    success: function (response) {
      if (response.status == 'ok') {
        popup_open('player-gameweek', response.data);
      }
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

$('body').on('change', '#sw-top-gw', function() {
  loadSelectedGameweek(this.value);
});

$('body').on('click', '.sw-gameweek .gameweek-selector-inline .btn', function() {
  loadSelectedGameweek(this.dataset.id);
});

$('body').on('click', '.sw-gameweek .gameweek-matches-summary .top .btn', function() {
  loadSelectedGameweek(this.dataset.gw);
});

$('body').on('click', '.sw-gameweek .btn-match', function() {
  $('.sw').animate({scrollTop: $('#gameweek-match-' + $(this).data('match')).offset().top - ($('.sw-topbar').height() + 12)}, 200);
});

$('body').on('click', '.popup-barter-players .player-avail', function() {
  var btn = $(this);
  popup_back();
  addBarterPlayer(btn.data());
  updateBarterInputs();
});

$('body').on('click', '.btn-barter-player', function() {
  var btn = $(this);
  removeBarterPlayer(btn.data());
  updateBarterInputs();
});

$('body').on('click', '.btn-giphy', function() {
  $(this).hide();
  $('.giphy-content').show();
  var search_texts = shuffle(['owned', 'deal with it', 'mic drop', 'boom', 'surprise', 'lol', 'facepalm', 'omg', 'wtf', 'haha']);
  giphyText = search_texts[0];
  giphySearch(giphyText);
  $('.giphy-results').on('scroll', function() {
    var w_content = $('.giphy-content').width();
    var w_scroll = $('.giphy-results').get(0).scrollWidth;
    var scrolled = $('.giphy-results').scrollLeft();
    if (w_scroll - w_content - 100 <= scrolled && !giphyLoading) {
      giphyLoading = true;
      giphyOffset += 10;
      giphySearch(giphyText, false);
    }
  });
});

$('body').on('input', '.giphy-input', function() {
  var text = $(this).val();
  if (text.length < 2) {
    return false;
  }
  giphyText = text;
  giphyOffset = 0;
  $('.giphy-results').scrollLeft(0);
  $('input[name=id_giphy]').val('');
  giphySearch(giphyText, true);
});

$('body').on('click', '.giphy-results .img', function() {
  var img = $(this);
  if (img.hasClass('selected')) {
    $('.giphy-results .img').removeClass('selected');
    $('input[name=id_giphy]').val('');
  } else {
    $('.giphy-results .img').removeClass('selected');
    img.addClass('selected');
    $('input[name=id_giphy]').val(img.data('id'));
  }
});

$('body').on('click', '.btn-enable-community', function() {
  var btn = $(this);
  btn_lock(btn);
  $.ajax({
    url: 'ajax/enable-community',
    success: function (response) {
      location.href = _FG_cfg.base + 'feed';
    },
    complete: function() {
      btn_unlock(btn);
    }
  });
});

$('body').on('click', '.btn-tutorial-close', function() {
  tutorialClose($(this).data('tutorial'));
});
$('body').on('click', '.btn-tutorial-skip', function() {
  tutorialClose($(this).data('tutorial'));
});

$('body').on('click', '.btn-tutorial-next', function() {
  var btn = $(this);
  btn_lock(btn);
  tutorialLoadStep(btn.data('next'));
});

$('body').on('input', '#toggle-theme', function() {
  var setTheme;
  var currentTheme = Cookies.get('theme') || _FG_cfg.brand.defaultTheme;
  setTheme = currentTheme == 'light-2025' ? 'dark-2025' : 'light-2025';
  Cookies.set('theme', setTheme, {expires: 365 * 10});
  fullReload();
});

$('#popup').on('input', '.input-sub-add input, #input-tel', function (e, from) {
  if (e.type == 'input') {
    update_popup_send();
  }
  var start = this.selectionStart,
    length = this.value.length,
    number = this.value;
  $(this).val(thousands(number));
  if (from != 'btn') {
    if (this.value.length < length) {
      start--;
    } else if (this.value.length > length) {
      start++;
    }
    if (e.keyCode == 8 && number.charAt(start - 1) == _FG_cfg.locale.thousands_sep) {
      start--;
    } else if (e.keyCode == 46 && number.charAt(start) == _FG_cfg.locale.thousands_sep) {
      start++;
    }
    start = (start < 0) ? 0 : start;
    this.setSelectionRange(start, start);
  }
  var elementUpdate = $(this).data('update');
  if (elementUpdate) {
    var value = this.value.replace(/\D/g, '');
    $(elementUpdate).attr('value', value);
    $(elementUpdate).val(value);
    update_range_bg($(elementUpdate).get(0));
  }
});

$('#popup').on('ontouchstart' in document ? 'touchstart' : 'mousedown', '.input-sub-add .num-sub, .input-sub-add .num-add', function (e) {
  e.preventDefault();
  var step = $(this).parent().data('step');
  var increment = $(this).parent().data('increment');
  update_popup_send();
  var action = $(this).hasClass('num-add') ? 'add' : 'sub';
  pressed = true;
  running(action, step, increment);
});

$('#popup').on('ontouchend' in document ? 'touchend touchmove touchcancel' : 'mouseup', '.input-sub-add .num-sub, .input-sub-add .num-add', function (e) {
  if (e.type == 'touchmove') {
    if (isTouchOutsideElement($(this), e)) {
      reset_press();
    }
  } else {
    reset_press();
  }
});

$('body').on('input', 'input[type=range]:not(.range-dual)', function () {
  update_range_bg(this);
  update_popup_send();
  const inputRange = $(this);
  var elementUpdate = $(this).data('update');
  if (elementUpdate) {
    const num = this.value;
    const newValStr = thousands(num);
    $(elementUpdate).attr('value', newValStr);
    $(elementUpdate).val(newValStr).trigger('input');

    inputRange.attr('value', num);
    inputRange.val(num);
  }
});

$('body').on('click', '.btn-app-rating', function() {
  var btn = $(this);
  if (btn.data('rating') == 'good') {
    ratingPromptText = trans('RATING_PROMPT_OK', { brand_name: _FG_cfg.brand.name, store_name: ratingPromptStore });
    ratingPromptButtons = '.app-rating-good';
  } else {
    ratingPromptText = trans('RATING_PROMPT_BAD');
    ratingPromptButtons = '.app-rating-bad';
  }
  writeRatingPromptBubble();
});

$('body').on('click', '.btn-app-rating-skip', function() {
  const created = _FG_user.created_ts;
  const now     = Math.ceil(new Date().getTime() / 1000);

  const diff = (now - created) / 60 / 60 / 24;

  if (diff < 7) {
    expires = 7;
  } else if (diff >= 7 && diff < 14) {
    expires = 14;
  } else if (diff >= 14 && diff < 90) {
    expires = 90;
  } else {
    expires = 365;
  }

  Cookies.set('review_skipped', 1, { expires: expires, path: '/' });
  ratingPrompt.removeClass('show');
});

$('body').on('click', '.btn-app-rating-link', function() {
  saveRatingClick();
  setTimeout(function() {
    ratingPrompt.removeClass('show');
  }, 500);
});

$(document).on('click', '.btn-sidebar-open', function() {
  toggleSidebar();
});

$(document).on('click', '.sidebar-open .sidebar-overlay', function() {
  toggleSidebar();
});

$('body').on('click', 'a[data-partial="true"]', function(event) {
  event.preventDefault();
  loadPartial($(this).attr('href'), 'link');
});

window.addEventListener('popstate', function(event) {
  if (location.pathname === currentPath) {
    event.preventDefault();
    return false;
  }
  loadPartial(location.href, 'history');
});

window.document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    return;
  }
  refreshStaticData();
  if (getCurrentTimestamp() - lastForegroundTimestamp > 60 * 15) {
    fullReload();
  }
  lastForegroundTimestamp = getCurrentTimestamp();
});

$('body').on('click', '[data-tracking]', function() {
  var btn = $(this);
  var json = JSON.parse(btn.attr('data-tracking'));
  commonAnalytics.trackClick(json.action, json.pageType);
});

$('body').on('click', '#squad-view-restore', function() {
  var defaultView = {
    sort: 'position',
    view: 'market'
  };
  _FG_cfg.squadView = {
    me: defaultView,
    users: defaultView
  };
  Cookies.remove('squad_view');
  reloadView();
});

$('body').on('click', '#squad-view-apply', function() {
  var profile = $('#squad-view input[name=profile]').val();
  _FG_cfg.squadView[profile] = {
    sort: $('#squad-view select[name=sort]').val(),
    view: $('#squad-view input[name=view]:checked').val()
  };
  Cookies.set('squad_view', JSON.stringify(_FG_cfg.squadView), { expires: 365 * 10, path: '/' });
  reloadView();
});

$('body').on('click', '#btn-redeem-coupon', function (event) {
  event.preventDefault();

  const btn = $(this);

  btn_lock(btn);

  const form = btn.parents("form");
  const formData = new FormData(form.get(0));
  const code = formData.get("coupon_code");

  $.ajax({
    type: "PUT",
    url: `/api2/store/coupon/${code}`,
    success: async function () {
      toast(trans('COUPON_REDEEMED'), 'green');

      updateBalance();
      fetchCredits(updateCredits);

      popup_close();

      const coupon = await fetchCouponWithCode(code);

      commonAnalytics.send('coupon_redeemed', {
        name: coupon.name,
        code,
        product_id: coupon.product_id
      });
    },
    complete: function () {
      btn_unlock(btn);
    }
  });
});

/**
 * Coupons
 */
// Note: This one is meant for the entry point to make the URL look prettier without a hash
$(function() {
  if (location.hash.length) {
    return;
  }

  if (location.pathname !== AUTOMATIC_REDEEM_COUPON_REDIRECT_PATH) {
    return;
  }

  const urlSearchParams = new URLSearchParams(window.location.search);
  if (!urlSearchParams.has(AUTOMATIC_REDEEM_COUPON_REDIRECT_QUERY_PARAM_NAME)) {
    return;
  }

  sws.store.post.coupon = urlSearchParams.get(AUTOMATIC_REDEEM_COUPON_REDIRECT_QUERY_PARAM_NAME);
  window.location.hash = "#store";
});

$('body').on('click', '[data-event]', function () {
  var name = $(this).data('event');
  var params = $(this).data('params') || null;
  var options = typeof getAnalyticsEventOptions === 'function'
    ? getAnalyticsEventOptions(this, false)
    : { amplitude: this.hasAttribute('data-event-amplitude') };
  sendEvent(name, params, options);
});

$('body').on('click', '.btn-collapse-sidebar-section', function() {
  var btn = $(this);
  $(`#sidebar ul[data-section="${btn.data('section')}"]`).toggle();
  btn.toggleClass('collapsed');
});

$('#popup').on('change', '#select-new-admin', function() {
  req_leave.admin = (parseInt($(this).val()) !== 0);
  $('#btn-send').prop('disabled', !(req_leave.admin && req_leave.check));
});

$('#popup').on('input', 'input.check', function() {
  var val = $(this).val().toLowerCase();
  var word = $(this).data('word').toLowerCase();
  req_leave.check = (val == word);
  if ($('#select-new-admin').length === 0) {
    req_leave.admin = true;
  }
  $('#btn-send').prop('disabled', !(req_leave.admin && req_leave.check));
});

$('#popup').on('click', '#btn-set', function() {
  var button = $(this);
  btn_lock(button);
  var exit = false;
  var action = $('form input[name=action]').val();
  if (action == 'member') {
    var member = $('form select[name=member]').val();
    if (member.indexOf('%%') > -1) {
      member = member.split('%%');
      var amount = $('form input[name=amount]').val().replace(/[\.|,]/g, '');
      add_member(member[0], member[1], amount);
      exit = true;
    } else {
      toast(trans('Debes elegir un miembro'), 'red');
    }
  }
  btn_unlock(button);
  if (exit) {
    popup_close();
  }
});

$('body').on('click', '#btn-admin-payments-send', function() {
  var btn = $(this);
  btn_lock(btn);
  var ul_data = $('#admin-payments-members li').map(function() {
    return $(this).data();
  }).get();
  var reason = $('input[name=reason]').val();
  if (ul_data.length > 0 && reason) {
    $.ajax({
      url: 'ajax/admin',
      data: 'action=payment&reason=' + reason + '&members=' + JSON.stringify(ul_data),
      success: function(response) {
        if (response.status == 'ok') {
          toast(trans('Bonificación realizada con éxito'));
          btn_unlock(btn);
          $('#admin-payments-members').empty();
          updateBalance(response.data.balance);
        }
      },
      complete: function() {
        btn_unlock(btn);
      }
    });
  } else {
    toast(trans('No has añadido ningún miembro o asunto'), 'red');
    btn_unlock(btn);
  }
});

$('body').on('click', '#btn-admin-cancel-transfers-send', function() {
  var transfers = $('input[name="transfers[]"]:checked:enabled').map(function() {
    return $(this).val();
  }).get();
  var twig_data = {
    transfers: transfers
  };
  if (transfers.length > 0) {
    popup_open('admin/tools/cancel-transfers/confirm', twig_data, $(this));
  } else {
    toast(trans('No has seleccionado ningún fichaje'), 'red');
  }
});

$('body').on('click', '.btn-user-actions', function() {
  $('.user-actions-menu').remove();
  var btn = $(this);
  var id = btn.data('id');
  var templateData = btn.data();
  templateData.cfg = _FG_cfg;
  templateData.user = _FG_user;
  templateData.data = _FG_data;
  template = Twig.twig({
    href: _FG_cfg.paths.views + '/ajax/admin/tools/members/actions.twig?' + _FG_cfg.twig,
    async: true,
    load: function (template) {
      output = template.render(templateData);
      $('#user-' + id).find('.user-list-actions').append(output);
      setTimeout(function() {
        $('#user-' + id).find('.user-actions-menu').addClass('show');
      }, 1);
    }
  });
});

$('body').on('click', '.btn-user-actions-close', function() {
  $('.user-actions-menu.show').removeClass('show');
  setTimeout(function() {
    $('.user-actions-menu').remove();
  }, 50);
});

$('body').on('click', '#btn-icon-reset', function() {
  $('.emoji-value').text(_FG_user.communities[_FG_user.id_community].flag_emoji);
  $('input[name=value]').val('');
});

$('body').on('change', 'select.market_speed', function() {
  if ($(this).val() == 1) {
    $('.mid-cycle-offer').removeClass('disabled');
  } else {
    $('.mid-cycle-offer').addClass('disabled');
  }
});

$('body').on('click', '.panels-emoji .btn', function() {
  var btn = $(this);
  $('.emoji-value').text(btn.text());
  $('input[name=value]').val(btn.text());
});

var touchstartY = 0;
var canPTR = false;

document.addEventListener('touchstart', e => {
  if (!['android', 'ios'].includes(_FG_cfg.app)) {
    return;
  }
  if (Array.from(document.documentElement.classList).includes('no-ptr')) {
    canPTR = false;
    return;
  }
  var ptr = document.querySelector('.pull-to-refresh');
  var scrolling = window.scrollY || $('#inner-content').scrollTop() || $('.sw').scrollTop();
  ptr.classList.remove('return');
  touchstartY = e.touches[0].clientY;
  if (scrolling > 0) {
    canPTR = false;
  } else {
    canPTR = true;
  }
});

document.addEventListener('touchmove', e => {
  if (!canPTR) {
    return;
  }
  var ptr = document.querySelector('.pull-to-refresh');
  var touchY = e.touches[0].clientY;
  var touchDiff = touchY - touchstartY;
  var topSpace = 50;
  if (touchDiff > 0) {
    ptr.style.top = Math.min((touchDiff - 50), topSpace) + 'px';
    ptr.style.transform = `rotate(${touchDiff * 3}deg)`;
    ptr.style.opacity = touchDiff / 100;
  }
});

document.addEventListener('touchend', e => {
  if (!canPTR) {
    return;
  }
  var ptr = document.querySelector('.pull-to-refresh');
  var touchY = e.changedTouches[0].clientY;
  var touchDiff = touchY - touchstartY;
  var topSpace = 50;
  if (ptr.offsetTop >= topSpace) {
    ptr.classList.add('rotating');
    location.reload();
  } else if (touchDiff != 0) {
    ptr.classList.add('return');
    ptr.style.top = '-50px';
    ptr.style.removeProperty('opacity');
  }
});
