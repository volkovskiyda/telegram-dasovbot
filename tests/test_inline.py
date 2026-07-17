import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from telegram.error import BadRequest

from dasovbot.models import VideoInfo, TemporaryInlineQuery
from tests.helpers import (
    make_user, make_inline_query, make_chosen_inline_result,
    make_update, make_context, make_state,
)


class TestInlineQueryHandler(unittest.IsolatedAsyncioTestCase):

    def _make_info(self, **kwargs):
        defaults = dict(title='Test Video', webpage_url='https://example.com/v1', caption='cap')
        defaults.update(kwargs)
        return VideoInfo(**defaults)

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_empty_query_returns_early(self, mock_extract):
        query_obj = make_inline_query(query='')
        update = make_update(inline_query=query_obj)
        context = make_context()

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_extract.assert_not_called()
        query_obj.answer.assert_not_called()

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_fresh_query_extracts_and_answers(self, mock_extract):
        info = self._make_info()
        mock_extract.return_value = info

        query_obj = make_inline_query(query='https://example.com/v1')
        update = make_update(inline_query=query_obj)
        state = make_state(animation_file_id='anim123')
        context = make_context(state=state)

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_extract.assert_awaited_once()
        query_obj.answer.assert_awaited_once()
        results = query_obj.answer.call_args[1].get('results') or query_obj.answer.call_args[0][0]
        self.assertEqual(len(results), 1)
        self.assertIn('https://example.com/v1', state.temporary_inline_queries)
        self.assertIn('inline_queries', context.user_data)

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_cached_results_skips_extract(self, mock_extract):
        cached_results = [MagicMock()]
        cached_inline_queries = {'rid': 'https://example.com/v1'}
        tiq = TemporaryInlineQuery(
            timestamp='20240101_120000',
            results=cached_results,
            inline_queries=cached_inline_queries,
        )
        state = make_state(temporary_inline_queries={'https://example.com/v1': tiq})

        query_obj = make_inline_query(query='https://example.com/v1')
        update = make_update(inline_query=query_obj)
        context = make_context(state=state)

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_extract.assert_not_called()
        query_obj.answer.assert_awaited_once()
        self.assertEqual(context.user_data['inline_queries'], cached_inline_queries)

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_ignored_query_answers_empty(self, mock_extract):
        tiq = TemporaryInlineQuery(timestamp='20240101_120000', ignored=True)
        state = make_state(temporary_inline_queries={'https://example.com/v1': tiq})

        query_obj = make_inline_query(query='https://example.com/v1')
        update = make_update(inline_query=query_obj)
        context = make_context(state=state)

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_extract.assert_not_called()
        query_obj.answer.assert_awaited_once_with(results=[])

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_extract_returns_none(self, mock_extract):
        mock_extract.return_value = None

        query_obj = make_inline_query(query='https://example.com/v1')
        update = make_update(inline_query=query_obj)
        context = make_context()

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        query_obj.answer.assert_awaited_once_with(results=[])

    @patch('dasovbot.handlers.inline.process_entries')
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_playlist_entries(self, mock_extract, mock_process_entries):
        entry1 = {'title': 'E1', 'webpage_url': 'https://example.com/e1', 'url': 'https://example.com/e1'}
        entry2 = {'title': 'E2', 'webpage_url': 'https://example.com/e2', 'url': 'https://example.com/e2'}
        mock_process_entries.return_value = [entry1, entry2]

        info = self._make_info(entries=[entry1, entry2])
        mock_extract.return_value = info

        query_obj = make_inline_query(query='https://example.com/playlist')
        update = make_update(inline_query=query_obj)
        state = make_state(animation_file_id='anim123')
        context = make_context(state=state)

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_process_entries.assert_called_once_with([entry1, entry2])
        query_obj.answer.assert_awaited_once()
        results = query_obj.answer.call_args[1].get('results') or query_obj.answer.call_args[0][0]
        self.assertEqual(len(results), 2)

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_bad_request_doesnt_crash(self, mock_extract):
        info = self._make_info()
        mock_extract.return_value = info

        query_obj = make_inline_query(query='https://example.com/v1')
        query_obj.answer.side_effect = BadRequest('query is too old')
        update = make_update(inline_query=query_obj)
        state = make_state(animation_file_id='anim123')
        context = make_context()
        context.bot_data['state'] = state

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)
        # Should not raise

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_generic_error_single_video_populates(self, mock_extract, mock_append):
        info = self._make_info()
        mock_extract.return_value = info

        query_obj = make_inline_query(query='https://example.com/v1')
        query_obj.answer.side_effect = RuntimeError('network')
        user = make_user(id=42)
        query_obj.from_user = user
        update = make_update(inline_query=query_obj)
        state = make_state(animation_file_id='anim123')
        context = make_context(state=state)

        from dasovbot.handlers.inline import inline_query_handler
        await inline_query_handler(update, context)

        mock_append.assert_awaited_once()
        # chat ids are strings everywhere else in the state
        self.assertEqual(mock_append.await_args.kwargs['chat_ids'], ['42'])


class TestChosenQuery(unittest.IsolatedAsyncioTestCase):

    async def test_no_inline_message_id_returns(self):
        result = make_chosen_inline_result(inline_message_id=None)
        update = make_update(chosen_inline_result=result)
        context = make_context()

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        context.bot.edit_message_media.assert_not_called()

    async def test_no_inline_queries_returns(self):
        result = make_chosen_inline_result()
        update = make_update(chosen_inline_result=result)
        context = make_context(user_data={})

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        context.bot.edit_message_media.assert_not_called()

    async def test_cached_file_id_edits_message(self):
        info = VideoInfo(title='Test', file_id='fid123', caption='cap', webpage_url='https://example.com/v1')
        state = make_state(videos={'https://example.com/v1': info})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': 'https://example.com/v1'}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        context.bot.edit_message_media.assert_awaited_once()
        call_kwargs = context.bot.edit_message_media.call_args[1]
        self.assertEqual(call_kwargs['inline_message_id'], 'imid1')

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_no_file_id_appends_intent(self, mock_append):
        state = make_state(videos={})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': 'https://example.com/v1'}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        call_kwargs = mock_append.call_args[1]
        self.assertEqual(call_kwargs['inline_message_id'], 'imid1')

    async def test_pops_inline_queries(self):
        info = VideoInfo(title='Test', file_id='fid123', caption='cap', webpage_url='https://example.com/v1')
        state = make_state(videos={'https://example.com/v1': info})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': 'https://example.com/v1'}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        self.assertNotIn('inline_queries', context.user_data)

    async def test_dict_format_query_data_edits_message(self):
        info = VideoInfo(title='Test', file_id='fid123', caption='cap', webpage_url='https://example.com/v1')
        state = make_state(videos={'https://example.com/v1': info})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': {'url': 'https://example.com/v1', 'upload_date': '20240101'}}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        context.bot.edit_message_media.assert_awaited_once()
        call_kwargs = context.bot.edit_message_media.call_args[1]
        self.assertEqual(call_kwargs['inline_message_id'], 'imid1')

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_dict_format_passes_upload_date_to_intent(self, mock_append):
        state = make_state(videos={})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': {'url': 'https://example.com/v1', 'upload_date': '20240515'}}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        call_kwargs = mock_append.call_args[1]
        self.assertEqual(call_kwargs['upload_date'], '20240515')
        self.assertEqual(call_kwargs['source'], 'inline')

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_recovers_query_data_when_user_data_overwritten(self, mock_append):
        # A later query overwrote user_data['inline_queries'], so the result the
        # user actually picked (rid_old) is only in temporary_inline_queries.
        tiq = TemporaryInlineQuery(
            timestamp='20240101_120000',
            results=[],
            inline_queries={'rid_old': {'url': 'https://example.com/old', 'upload_date': '20240101'}},
        )
        state = make_state(
            videos={},
            temporary_inline_queries={'https://example.com/old': tiq},
        )

        result = make_chosen_inline_result(result_id='rid_old', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid_new': {'url': 'https://example.com/new'}}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        self.assertEqual(mock_append.call_args[0][0], 'https://example.com/old')

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_title_lookup_from_temporary_inline_queries(self, mock_append):
        cached_result = MagicMock()
        cached_result.id = 'rid1'
        cached_result.title = 'Found Title'
        tiq = TemporaryInlineQuery(
            timestamp='20240101_120000',
            results=[cached_result],
            inline_queries={'rid1': {'url': 'https://example.com/v1', 'upload_date': '20240101'}},
        )
        state = make_state(
            videos={},
            temporary_inline_queries={'https://example.com/v1': tiq},
        )

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': {'url': 'https://example.com/v1', 'upload_date': '20240101'}}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        call_kwargs = mock_append.call_args[1]
        self.assertEqual(call_kwargs['title'], 'Found Title')

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_title_none_when_not_in_cache(self, mock_append):
        state = make_state(videos={}, temporary_inline_queries={})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': {'url': 'https://example.com/v1', 'upload_date': '20240101'}}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        call_kwargs = mock_append.call_args[1]
        self.assertIsNone(call_kwargs['title'])

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_string_format_backward_compat(self, mock_append):
        """String query_data (legacy format) sets upload_date to None"""
        state = make_state(videos={})

        result = make_chosen_inline_result(result_id='rid1', inline_message_id='imid1')
        update = make_update(chosen_inline_result=result)
        context = make_context(
            state=state,
            user_data={'inline_queries': {'rid1': 'https://example.com/v1'}},
        )

        from dasovbot.handlers.inline import chosen_query
        await chosen_query(update, context)

        mock_append.assert_awaited_once()
        call_kwargs = mock_append.call_args[1]
        self.assertIsNone(call_kwargs['upload_date'])


class TestInlineAnswerErrors(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_ignored_query_answer_error_swallowed(self, mock_extract):
        from dasovbot.handlers.inline import inline_query_handler
        state = make_state(temporary_inline_queries={'q': TemporaryInlineQuery(ignored=True)})
        iq = make_inline_query('q')
        iq.answer.side_effect = Exception('timed out')
        await inline_query_handler(make_update(inline_query=iq), make_context(state=state))
        mock_extract.assert_not_awaited()

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_cached_results_answer_error_swallowed(self, mock_extract):
        from dasovbot.handlers.inline import inline_query_handler
        tiq = TemporaryInlineQuery(results=[MagicMock()], inline_queries={'id': 'q'})
        state = make_state(temporary_inline_queries={'q': tiq})
        iq = make_inline_query('q')
        iq.answer.side_effect = Exception('timed out')
        context = make_context(state=state)
        await inline_query_handler(make_update(inline_query=iq), context)
        mock_extract.assert_not_awaited()
        self.assertEqual(context.user_data['inline_queries'], tiq.inline_queries)

    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock, return_value=None)
    async def test_no_info_answer_error_swallowed(self, mock_extract):
        from dasovbot.handlers.inline import inline_query_handler
        state = make_state()
        iq = make_inline_query('q')
        iq.answer.side_effect = Exception('timed out')
        await inline_query_handler(make_update(inline_query=iq), make_context(state=state))
        mock_extract.assert_awaited_once()

    @patch('dasovbot.handlers.inline.process_entries', return_value=[])
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_playlist_with_no_usable_entries_answers_empty(self, mock_extract, mock_entries):
        from dasovbot.handlers.inline import inline_query_handler
        mock_extract.return_value = VideoInfo(title='playlist', entries=[{'id': '1'}])
        state = make_state()
        iq = make_inline_query('q')
        await inline_query_handler(make_update(inline_query=iq), make_context(state=state))
        iq.answer.assert_awaited_once_with(results=[], cache_time=1)


class TestInlineNoUsableFileId(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_single_video_without_animation_queues_download(self, mock_extract, mock_append):
        from dasovbot.handlers.inline import inline_query_handler
        mock_extract.return_value = VideoInfo(title='T', webpage_url='https://example.com/v1')
        state = make_state(animation_file_id=None)
        iq = make_inline_query('https://example.com/v1', from_user=make_user(id=42))
        await inline_query_handler(make_update(inline_query=iq), make_context(state=state))
        iq.answer.assert_awaited_once_with(results=[], cache_time=1)
        mock_append.assert_awaited_once()
        self.assertEqual(mock_append.await_args.kwargs['chat_ids'], ['42'])

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    @patch('dasovbot.handlers.inline.extract_info', new_callable=AsyncMock)
    async def test_cached_file_id_still_served_without_animation(self, mock_extract, mock_append):
        from dasovbot.handlers.inline import inline_query_handler
        mock_extract.return_value = VideoInfo(
            title='T', webpage_url='https://example.com/v1', file_id='fid')
        state = make_state(animation_file_id=None)
        iq = make_inline_query('https://example.com/v1')
        await inline_query_handler(make_update(inline_query=iq), make_context(state=state))
        results = iq.answer.await_args.kwargs['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].video_file_id, 'fid')
        mock_append.assert_not_awaited()


class TestChosenQueryUnknownResult(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_unknown_result_id_returns_early(self, mock_append):
        from dasovbot.handlers.inline import chosen_query
        state = make_state()
        result = make_chosen_inline_result(result_id='unknown')
        context = make_context(state=state, user_data={'inline_queries': {'other': 'url'}})
        await chosen_query(make_update(chosen_inline_result=result), context)
        mock_append.assert_not_awaited()


class TestPopulateVideoFallback(unittest.IsolatedAsyncioTestCase):
    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_cached_file_id_skips_intent(self, mock_append):
        from dasovbot.handlers.inline import _populate_video
        info = VideoInfo(title='T', file_id='fid')
        state = make_state(videos={'q': info})
        result = await _populate_video('q', ['1'], state)
        self.assertIs(result, info)
        mock_append.assert_not_awaited()

    @patch('dasovbot.handlers.inline.append_intent', new_callable=AsyncMock)
    async def test_uncached_appends_intent(self, mock_append):
        from dasovbot.handlers.inline import _populate_video
        state = make_state()
        await _populate_video('q', ['1'], state)
        mock_append.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
