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
  it('rejects HEIC before uploading and tells the operator how to proceed', async () => {
    const onUploaded = vi.fn();
    render(<UploadImagePanel onUploaded={onUploaded} />);

    chooseFile(new File(['heic'], 'records.HEIC', { type: 'image/heic' }));

    expect(await screen.findByText(/HEIC\/HEIF photos are not supported/i)).toBeInTheDocument();
    expect(mockPostForm).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('uploads a supported image and hands the job id back to the page', async () => {
    mockPostForm.mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      filename: 'records.png',
    });
    const onUploaded = vi.fn();
    render(<UploadImagePanel onUploaded={onUploaded} />);

    chooseFile(new File(['png'], 'records.png', { type: 'image/png' }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith('job-1', 'records.png'));
    expect(mockPostForm).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Accepted formats: JPG, PNG, WebP, GIF, BMP or TIFF/)).toBeInTheDocument();
  });
});
