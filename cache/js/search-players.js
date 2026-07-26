searchPlayers = {
  post: 'players',
  filters: {
    position: 0,
    value_from: 0,
    value_to: 0,
    clause_from: 0,
    clause_to: 0,
    team: 0,
    injured: 0,
    favs: 0,
    owner: 0,
    benched: 0,
    stealable: 0
  },
  offset: 0,
  order: 0,
  name: '',
  parentElement: '#fg-content'
};

_FG_data.searchPagePlayers = JSON.parse(JSON.stringify(searchPlayers));

_FG_data.searchPlayers = {
  get: function(scope = 'pagePlayers') {
    return _FG_data.searchPagePlayers;
  }
};

const handleSearchPlayersInputChange = debounce(function(input) {
  var scope = input.data('scope');
  if (_FG_data.searchPlayers.get(scope).name.length < 1 && input.val() === '') {
    return;
  }
  _FG_data.searchPlayers.get(scope).name = input.val();
  _FG_data.searchPlayers.get(scope).offset = 0;
  findPlayers(scope);
}, 500);

$('body').on('keyup', '.search-players-input', function() {
  var input = $(this);
  handleSearchPlayersInputChange(input);
});

function findPlayers(scope, loadingElement = null) {
  var parentElement = $(_FG_data.searchPlayers.get(scope).parentElement);
  if (!loadingElement) {
    loadingElement = parentElement;
  }
  loadingElement.addClass('loading');
  $.ajax({
    url: 'ajax/sw/players',
    data: _FG_data.searchPlayers.get(scope),
    success: function(response) {
      if (response.data.players) {
        if (response.data.players.length < 1) {
          parentElement.find('.search-players-empty .empty').show();
          parentElement.find('.search-players-more').hide();
        } else {
          parentElement.find('.search-players-empty .empty').hide();
          if (response.data.players.length < 50) {
            parentElement.find('.search-players-more').hide();
          } else {
            parentElement.find('.search-players-more').show();
          }
        }
        var template = Twig.twig({
          href: _FG_cfg.paths.views + '/components/search/player-rows.twig?' + _FG_cfg.twig,
          load: function(template) {
            response.data.cfg = _FG_cfg;
            response.data.user = _FG_user;
            var output = template.render(response.data);
            if (_FG_data.searchPlayers.get(scope).offset > 0) {
              parentElement.find('.search-players-list').append(output);
            } else {
              parentElement.find('.search-players-list').html(output);
            }
          }
        });
      }
    },
    complete: function() {
      loadingElement.removeClass('loading');
    }
  });
}

$('body').on('click', '.search-players-more', function() {
  var scope = $(this).data('scope');
  _FG_data.searchPlayers.get(scope).offset += 50;
  findPlayers(scope, $(this));
});

$('body').on('click', '.search-players-filter-favs', function() {
  var btn = $(this);
  var scope = btn.data('scope');
  _FG_data.searchPlayers.get(scope).filters.favs = 1 - _FG_data.searchPlayers.get(scope).filters.favs;
  mapFiltersAndLoad(scope, true);
});

function mapFiltersAndLoad(scope, onlyFavs = false) {
  var parentElement = $(_FG_data.searchPlayers.get(scope).parentElement);
  var filterBtn = parentElement.find('.search-players-filter-btn');
  var filters = {};
  $('.search-players-filter-form').serializeArray().map(function(x) {
    if (x.value == 'on') {
      x.value = 1;
    }
    filters[x.name] = parseInt(x.value);
  });
  if (!onlyFavs) {
    for (var filter in _FG_data.searchPlayers.get(scope).filters) {
      if (typeof filters[filter] !== 'undefined' && filters[filter] != 0) {
        _FG_data.searchPlayers.get(scope).filters[filter] = filters[filter];
      } else {
        _FG_data.searchPlayers.get(scope).filters[filter] = 0;
      }
    }
    if (arePlayersFiltered(scope)) {
      filterBtn.addClass('btn--accent');
      filterBtn.removeClass('btn--primary btn--secondary btn--tertiary');
    } else {
      filterBtn.removeClass('btn--accent');
      filterBtn.addClass(filterBtn.data('style'));
    }
  }
  if (_FG_data.searchPlayers.get(scope).filters.favs) {
    svgChangeIcon(parentElement.find('.search-players-filter-favs'), 'star-filled');
  } else {
    svgChangeIcon(parentElement.find('.search-players-filter-favs'), 'star');
  }
  _FG_data.searchPlayers.get(scope).offset = 0;
  findPlayers(scope);
}

function arePlayersFiltered(scope) {
  for (var key of Object.keys(searchPlayers.filters)) {
    if (_FG_data.searchPlayers.get(scope).filters[key] != searchPlayers.filters[key]) {
      return true;
    }
  }
  return false;
}

$('body').on('click', '.search-players-filter-apply', function () {
  var scope = $(this).data('scope');
  mapFiltersAndLoad(scope);
  popup_close();
});

$('body').on('click', '.search-players-filter-reset', function () {
  var scope = $(this).data('scope');
  $('.search-players-filter-form input[value=0]').prop('checked', true);
  $('.search-players-filter-form select').val(0);
  $('.search-players-filter-form input[type=checkbox]').prop('checked', false);
  $('.search-players-filter-form input[name=value_from]').val(0);
  $('.search-players-filter-form input[name=value_to]').val(_FG_data.highestValue);
  $('.search-players-filter-form input[name=clause_from]').val(0);
  $('.search-players-filter-form input[name=clause_to]').val(_FG_data.highestClause);
  mapFiltersAndLoad(scope);
  popup_close();
});

$('body').on('change', '.search-players-sort, .search-players-sort-hidden', function () {
  var scope = $(this).data('scope');
  _FG_data.searchPlayers.get(scope).offset = 0;
  _FG_data.searchPlayers.get(scope).order = this.value;
  findPlayers(scope);
  var sortBtn = $('.search-players-sort-btn');
  if (this.value != 0) {
    sortBtn.addClass('btn--accent');
    sortBtn.removeClass('btn--secondary');
  } else {
    sortBtn.removeClass('btn--accent');
    sortBtn.addClass('btn--secondary');
  }
});

$('body').on('click', '.btn-search-players', function() {
  sendEvent('select_mag', {from: _FG_cfg.pag});
});

$('body').on('click', '.search-players-sort-btn', function() {
  $('.search-players-sort-hidden').focus().get(0).showPicker();
});

function dualRangeNumberFormat(value, max) {
  if (value == parseInt(max)) {
    return trans('Máx.');
  }
  return Number(value).toLocaleString(_FG_cfg.language.locale.replace('_', '-'));
}

function dualRangeUpdate(range) {
  let dualRangeWrapper = range.parentElement;
  let dualRangeFrom = dualRangeWrapper.querySelector('.range-dual-from');
  let dualRangeTo = dualRangeWrapper.querySelector('.range-dual-to');
  let dualRangeType = dualRangeWrapper.dataset.rangeDualType;
  let dualRangeMax = dualRangeWrapper.dataset.rangeDualMax;
  let dualRangeFromValueEl = document.querySelector('.range-values[data-range-dual-type="' + dualRangeType + '"] .value-from');
  let dualRangeToValueEl = document.querySelector('.range-values[data-range-dual-type="' + dualRangeType + '"] .value-to');

  let from = parseInt(dualRangeFrom.value);
  let to = parseInt(dualRangeTo.value);
  let min = parseInt(dualRangeFrom.min);
  let max = parseInt(dualRangeFrom.max);
  let gap = 100000;

  if (to - from < gap) {
    if (event?.target === dualRangeFrom) {
      from = to - gap;
      dualRangeFrom.value = from;
    } else {
      to = from + gap;
      dualRangeTo.value = to;
    }
  }

  let fromPercent = ((from - min) / (max - min)) * 100;
  let toPercent = ((to - min) / (max - min)) * 100;

  dualRangeWrapper.style.setProperty('--from', `${fromPercent}%`);
  dualRangeWrapper.style.setProperty('--to', `${toPercent}%`);

  dualRangeFromValueEl.textContent = dualRangeNumberFormat(from, dualRangeMax);
  dualRangeToValueEl.textContent = dualRangeNumberFormat(to, dualRangeMax);
}