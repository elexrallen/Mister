sws['market/offers-received'] = {
  post: 'offers-received'
};

sws['market/offers-sent'] = {
  post: 'offers-sent'
};

attach_scroll($(window), '.footer-sticky-market');

function runAfterPageLoad_market() {
  executeOrPostponeFunction('marketTimer');
  if ($('.show-bids-cta').length > 0) {
    sendEvent('show_bids_cta_seen');
  }
}

runAfterPageLoad_market();