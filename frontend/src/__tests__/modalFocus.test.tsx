import { describe, it, expect } from 'vitest';
import { useState } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal, ModalButton } from '../components/ui/Modal';

// Keyboard access in a dialog: focus moves in, cannot Tab out into the page
// behind the overlay, Escape closes, and focus returns to the control that
// opened it. The trap used to be absent, so Tab walked onto the hidden page.

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Test dialog"
        footer={
          <>
            <ModalButton tone="ghost" onClick={() => setOpen(false)}>
              Cancel
            </ModalButton>
            <ModalButton onClick={() => setOpen(false)}>Save</ModalButton>
          </>
        }
      >
        <label>
          Amount
          <input aria-label="Amount" />
        </label>
      </Modal>
    </>
  );
}

describe('Modal keyboard behaviour', () => {
  it('moves focus into the dialog on open and back to the trigger on close', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const opener = screen.getByRole('button', { name: 'Open dialog' });
    await user.click(opener);

    const dialog = await screen.findByRole('dialog');
    // Focus starts on the dialog itself, so the next Tab lands on its first control.
    expect(document.activeElement).toBe(dialog);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // Focus returns to the button that opened it, not to the document top.
    expect(document.activeElement).toBe(opener);
  });

  it('keeps Tab inside the dialog instead of walking onto the page behind it', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Open dialog' }));

    const dialog = await screen.findByRole('dialog');
    const closeButton = within(dialog).getByRole('button', { name: 'Close dialog' });
    const saveButton = within(dialog).getByRole('button', { name: 'Save' });

    // Tab forward from the last control wraps to the first.
    saveButton.focus();
    await user.tab();
    expect(document.activeElement).toBe(closeButton);

    // Shift+Tab from the first control wraps to the last.
    closeButton.focus();
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(saveButton);
  });
});
