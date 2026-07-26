function runAfterPageLoad_team() {
  executeOrPostponeFunction('player_list_border');
  executeOrPostponeFunction('scrollToLastMatch');
}

runAfterPageLoad_team();

sws.subs.post.id_gameweek = _FG_data.gameWeekId;