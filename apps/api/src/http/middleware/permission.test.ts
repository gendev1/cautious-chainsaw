import { describe, it, expect } from 'vitest';
import { assertPermission } from './permission.js';
import { ForbiddenError } from '../../shared/errors.js';
import { mockFirmAdmin, mockAdvisor, mockViewer } from '../../test/helpers.js';

describe('permission middleware — assertPermission', () => {
  describe('core behaviour', () => {
    it('passes when the actor has the required permission', () => {
      const actor = mockFirmAdmin();
      expect(() => assertPermission(actor, 'client.read')).not.toThrow();
    });

    it('throws ForbiddenError when the actor lacks the permission', () => {
      const actor = mockViewer(); // does not have order.submit
      expect(() => assertPermission(actor, 'order.submit')).toThrow(ForbiddenError);
      expect(() => assertPermission(actor, 'order.submit')).toThrow(
        /Missing required permission: order\.submit/,
      );
    });
  });

  describe('mockFirmAdmin (all permissions)', () => {
    const actor = mockFirmAdmin();

    it('passes for any permission in the set', () => {
      for (const perm of actor.permissions) {
        expect(() => assertPermission(actor, perm)).not.toThrow();
      }
    });

    it('passes for user.manage_roles', () => {
      expect(() => assertPermission(actor, 'user.manage_roles')).not.toThrow();
    });

    it('passes for support.impersonate', () => {
      expect(() => assertPermission(actor, 'support.impersonate')).not.toThrow();
    });
  });

  describe('mockViewer (read-only)', () => {
    const actor = mockViewer();

    it('passes for client.read', () => {
      expect(() => assertPermission(actor, 'client.read')).not.toThrow();
    });

    it('fails for order.submit', () => {
      expect(() => assertPermission(actor, 'order.submit')).toThrow(ForbiddenError);
    });

    it('fails for user.manage_roles', () => {
      expect(() => assertPermission(actor, 'user.manage_roles')).toThrow(ForbiddenError);
    });
  });

  describe('mockAdvisor', () => {
    const actor = mockAdvisor();

    it('passes for client.read', () => {
      expect(() => assertPermission(actor, 'client.read')).not.toThrow();
    });

    it('passes for order.submit', () => {
      expect(() => assertPermission(actor, 'order.submit')).not.toThrow();
    });

    it('fails for billing.post', () => {
      expect(() => assertPermission(actor, 'billing.post')).toThrow(ForbiddenError);
    });

    it('fails for user.manage_roles', () => {
      expect(() => assertPermission(actor, 'user.manage_roles')).toThrow(ForbiddenError);
    });
  });
});
