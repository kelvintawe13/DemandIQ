import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  api: { postForm: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { UploadImagePanel } from '../components/UploadImagePanel';

const mockPostForm = api.postForm as unknown as ReturnType<typeof vi.fn>;

function chooseFile(file: File) {
  fireEvent.change(screen.getByLabelText('Handwritten record photo'), {
    target: { files: [file] },
  });
}

function acceptConsent() {
  fireEvent.click(
    screen.getByLabelText(/I have permission to upload this photo/i),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, 'createObjectURL', {
    writable: true,
    value: vi.fn(() => 'blob:preview'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    writable: true,
    value: vi.fn(),
  });
});

describe('UploadImagePanel', () => {
  it('requires photo-upload consent before sending anything', async () => {
    /* Signing up accepts the policy; that is not permission to upload a
       photograph of someone else's log. The file is refused until the box is
       ticked, so the promise on the policy page is kept by the component. */
    const onUploaded = vi.fn();
    render(<UploadImagePanel onUploaded={onUploaded} />);

    chooseFile(new File(['png'], 'records.png', { type: 'image/png' }));

    expect(
      await screen.findByText(/Confirm you have permission to upload this photo/i),
    ).toBeInTheDocument();
    expect(mockPostForm).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Choose Image/i })).toBeDisabled();
  });

  it('rejects HEIC before uploading and tells the operator how to proceed', async () => {
    const onUploaded = vi.fn();
    render(<UploadImagePanel onUploaded={onUploaded} />);
    acceptConsent();

    chooseFile(new File(['heic'], 'records.HEIC', { type: 'image/heic' }));

    expect(await screen.findByText(/HEIC\/HEIF photos are not supported/i)).toBeInTheDocument();
    expect(mockPostForm).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('sends the reporting year that yearless dates should be written in', async () => {
    /* "12 Oct" on the page has no year; the year the operator states is what
       turns it into a real date. Sending nothing would silently use today's
       year and misfile an archive page as this year's demand. */
    mockPostForm.mockResolvedValue({
      job_id: 'job-1',
      status: 'ready',
      filename: 'records.png',
    });
    render(<UploadImagePanel onUploaded={vi.fn()} />);
    acceptConsent();

    const yearField = screen.getByLabelText(/Year for dates without one/i);
    expect((yearField as HTMLInputElement).value).toBe(String(new Date().getFullYear()));
    fireEvent.change(yearField, { target: { value: '2024' } });

    chooseFile(new File(['png'], 'records.png', { type: 'image/png' }));

    await waitFor(() => expect(mockPostForm).toHaveBeenCalledTimes(1));
    const form = mockPostForm.mock.calls[0][1] as FormData;
    expect(form.get('reporting_year')).toBe('2024');
    expect((form.get('file') as File).name).toBe('records.png');
  });

  it('uploads a supported image and hands the job id back to the page', async () => {
    mockPostForm.mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      filename: 'records.png',
    });
    const onUploaded = vi.fn();
    render(<UploadImagePanel onUploaded={onUploaded} />);
    acceptConsent();

    chooseFile(new File(['png'], 'records.png', { type: 'image/png' }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith('job-1', 'records.png'));
    expect(mockPostForm).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Accepted formats: JPG, PNG, WebP, GIF, BMP or TIFF/)).toBeInTheDocument();
  });
});
