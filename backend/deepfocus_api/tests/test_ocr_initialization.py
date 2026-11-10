"""Exercise OCR slot ownership without requiring an external OCR executable."""
import threading
import unittest
from unittest.mock import patch
from deepfocus_api import research_vision as vision


class OcrInitializationTests(unittest.TestCase):
    def test_nonempty_input_reaches_worker(self):
        with patch.object(vision, '_extract_pdf_ocr_text_locked', return_value='source text') as worker:
            self.assertEqual(vision.extract_pdf_ocr_text(b'pdf', max_pages=1), 'source text')
            worker.assert_called_once_with(b'pdf', max_pages=1)

    def test_worker_exception_releases_slot(self):
        slot = threading.BoundedSemaphore(1)
        with patch.object(vision, '_OCR_SEMAPHORE', slot), patch.object(vision, '_extract_pdf_ocr_text_locked', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                vision.extract_pdf_ocr_text(b'pdf')
            self.assertTrue(slot.acquire(blocking=False))
            slot.release()

    def test_busy_slot_does_not_start_worker(self):
        slot = threading.BoundedSemaphore(1)
        slot.acquire()
        with patch.object(vision, '_OCR_SEMAPHORE', slot), patch.object(vision, '_OCR_SLOT_WAIT_SECONDS', 0), patch.object(vision, '_extract_pdf_ocr_text_locked') as worker:
            self.assertEqual(vision.extract_pdf_ocr_text(b'pdf'), '')
            worker.assert_not_called()
            self.assertFalse(slot.acquire(blocking=False))
        slot.release()

    def test_empty_input_never_acquires_slot(self):
        with patch.object(vision, '_OCR_SEMAPHORE') as slot:
            self.assertEqual(vision.extract_pdf_ocr_text(b''), '')
            slot.acquire.assert_not_called()
