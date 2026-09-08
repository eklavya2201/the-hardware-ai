import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { Pencil } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { supabase } from '@/lib/supabase';

/**
 * One row of the sidebar's Creations list, renamable in place.
 *
 * Renaming used to mean opening the history page and its dialog. A creation's
 * name is the only handle you have on it once there are ten of them, so it is
 * editable where you actually read it: double-click the name, or the pencil
 * that appears on hover. Enter or blur commits, Escape reverts.
 */
export function RenamableCreation({
  id,
  title,
  onNavigate,
}: {
  id: string;
  title: string;
  onNavigate?: () => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editing) setDraft(title);
  }, [title, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  const rename = useMutation({
    mutationFn: async (newTitle: string) => {
      const { error } = await supabase
        .from('conversations')
        .update({ title: newTitle })
        .eq('id', id);
      if (error) throw error;
    },
    // Both lists read the same rows: the sidebar's recent ten and the history
    // page's full set.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
    onError: () => {
      setDraft(title);
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
  });

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (!next || next === title) {
      setDraft(title);
      return;
    }
    // Paint the new name immediately; the invalidate above settles it.
    queryClient.setQueriesData<{ id: string; title: string }[]>(
      { queryKey: ['conversations'] },
      (old) =>
        Array.isArray(old) ? old.map((c) => (c.id === id ? { ...c, title: next } : c)) : old,
    );
    rename.mutate(next);
  };

  if (editing) {
    return (
      <li>
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') {
              setDraft(title);
              setEditing(false);
            }
          }}
          className="w-full rounded-md border border-sq-blue/60 bg-sq-background-1 p-1 text-xs font-medium text-sq-neutral-10 outline-none"
        />
      </li>
    );
  }

  return (
    <li className="group/creation flex items-center gap-1">
      <Link
        to="/editor/$id"
        params={{ id }}
        onClick={onNavigate}
        onDoubleClick={(e) => {
          e.preventDefault();
          setEditing(true);
        }}
        className="min-w-0 flex-1"
      >
        <span className="line-clamp-1 block text-ellipsis text-nowrap rounded-md p-1 text-xs font-medium text-sq-neutral-400 transition-colors duration-200 ease-in-out [@media(hover:hover)]:group-hover/creation:bg-sq-neutral-950 [@media(hover:hover)]:group-hover/creation:text-sq-neutral-10">
          {draft}
        </span>
      </Link>
      <button
        type="button"
        aria-label={`Rename ${title}`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setEditing(true);
        }}
        className="shrink-0 rounded p-1 text-sq-neutral-500 opacity-0 transition-opacity hover:text-sq-neutral-10 focus-visible:opacity-100 group-hover/creation:opacity-100"
      >
        <Pencil className="h-3 w-3" />
      </button>
    </li>
  );
}
