CREATE TABLE stock_loss_return_stops (
	id CHAR(32) NOT NULL,
	root_disposition_id CHAR(32) NOT NULL,
	reversal_id CHAR(32) NOT NULL,
	return_operation_id CHAR(32) NOT NULL,
	return_line_id CHAR(32) NOT NULL,
	evidence_fingerprint VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_loss_return_stop_operation UNIQUE (return_operation_id),
	CONSTRAINT uq_loss_return_stop_line UNIQUE (return_line_id),
	CONSTRAINT uq_loss_return_stop_inverse UNIQUE (reversal_id),
	CONSTRAINT uq_loss_return_stop_root UNIQUE (root_disposition_id),
	CONSTRAINT fk_loss_return_stop_inverse_root FOREIGN KEY(reversal_id, root_disposition_id) REFERENCES stock_loss_disposition_reversals (id, root_disposition_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
	CONSTRAINT ck_loss_return_stop_fingerprint CHECK (length(evidence_fingerprint)=64),
	FOREIGN KEY(root_disposition_id) REFERENCES stock_loss_dispositions (id) ON DELETE RESTRICT,
	FOREIGN KEY(return_operation_id) REFERENCES stock_operation_orders (id) ON DELETE RESTRICT,
	FOREIGN KEY(return_line_id) REFERENCES stock_operation_lines (id) ON DELETE RESTRICT
);
