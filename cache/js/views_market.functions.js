function callback_offer(response) {
  var btn = getBidBtn(response);
  updateReceivedOffersCount(response.offers);
  btn.parents('li').remove();
  toast(trans('Jugador vendido'), 'green');
  updateBalance(response.balance);
}

function market_filter() {
  var empty = true;
  var players = $('#list-on-sale li');
  var prices = {
    0: [0, 999999999],
    1: [0, 1000000],
    2: [1000001, 5000000],
    3: [5000001, 10000000],
    4: [10000001, 999999999]
  };
  _FG_data.filters.loanable = 0;
  if (_FG_data.filters.owner === 2) {
    _FG_data.filters.loanable = 1;
    _FG_data.filters.owner = -1;
  }
  players.each(function () {
    var e = $(this);
    if (
      (e.data('position') == _FG_data.filters.position || _FG_data.filters.position === 0) &&
      (e.data('price') >= prices[_FG_data.filters.price][0] && e.data('price') <= prices[_FG_data.filters.price][1]) &&
      ((e.data('owner') > 0 && _FG_data.filters.owner == 1) || (e.data('owner') == _FG_data.filters.owner) || _FG_data.filters.owner === -1) &&
      (e.data('loanable') == _FG_data.filters.loanable)
    ) {
      e.show();
      empty = false;
    } else {
      e.hide();
    }
  });
  if (empty) {
    $('.empty').show();
  } else {
    $('.empty').hide();
  }
  setFilterButton();
}

function getFormattedTimer(remaining) {
  txt = '';
  days = Math.floor(remaining / 86400);
  remaining = remaining - days * 86400;
  hours = Math.floor(remaining / 3600);
  remaining = remaining - hours * 3600;
  minutes = Math.floor(remaining / 60);
  seconds = Math.floor(remaining % 60);
  if (days > 0) {
    txt = days + 'd ';
  }
  if (hours > 0) {
    txt = txt + hours + 'h ';
  }
  if (minutes > 0 && days === 0) {
    txt = txt + minutes + 'm ';
  }
  if (hours === 0 && days === 0) {
    txt = txt + seconds + 's';
  }

  return txt;
}
function marketTimer() {
  var now = new Date().getTime() / 1000;
  var remaining, hours, minutes, seconds;
  var txt;
  var auctions = 0;
  $('#list-on-sale li[data-owner]').each(function () {
    auctions++;
    remaining = $(this).data('ends') - now;
    if (remaining > 1) {
      txt = getFormattedTimer(remaining);
      $(this).find('.timer').text(trans('ENDS_IN_N', { time: txt }));
    }
  });
  if (auctions > 0) {
    setTimeout(marketTimer, 1000);
  }
}

function loanOfferExpirationTimer() {
  var offers = 0;
  $('li[data-id-offer]').each(function() {
    offers++;
    var hiddenInput = $(this).find('input[type="hidden"]');
    var span = $(this).find('span.loan-end');

    if (hiddenInput.length && span.length) {
      var expiryTime = parseInt(hiddenInput.val());
      const now = Date.now() / 1000;

      const remaining = expiryTime - now;
      const time= getFormattedTimer(remaining);
      span.text(`${trans('ENDS_IN_N', { time })}`);
    }
  });
  if (offers > 0) {
    setTimeout(loanOfferExpirationTimer, 10000);
  }
}

function setFilterButton() {
  if (_FG_data.filters.position === 0 && _FG_data.filters.price === 0 && _FG_data.filters.owner === -1 && _FG_data.filters.loanable === 0) {
    $('#btn-filter-market').removeClass('btn--accent');
    $('#btn-filter-market').addClass('btn--secondary');
  } else {
    $('#btn-filter-market').addClass('btn--accent');
    $('#btn-filter-market').removeClass('btn--secondary btn--tertiary');
  }
}

function callback_sw_market_offers_sent() {
  $('.footer-sticky').clone().appendTo('.sw-content');
  $('.footer-sticky-placeholder').clone().appendTo('.sw-content');
}

function callback_sw_market_offers_received() {
  callback_sw_market_offers_sent();
}

function open_market_offers_received_direct_transfer_confirmation() {
  updateFutureBalance(-$('input[name=amount]').val());
}
