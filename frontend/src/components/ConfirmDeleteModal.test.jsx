import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConfirmDeleteModal from "./ConfirmDeleteModal.jsx";

describe("ConfirmDeleteModal", () => {
  it("does not render when closed", () => {
    render(
      <ConfirmDeleteModal open={false} message="gone" onConfirm={() => {}} onCancel={() => {}} />
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("calls confirm and cancel handlers", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDeleteModal
        open
        message='This will permanently remove the scan for "payments.csv" and its reports.'
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/payments.csv/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
